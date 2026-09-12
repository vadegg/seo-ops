"""Read-only SEO feedback: equal-period trends and rotating index inspection.

Report failures never erase a previous successful report or masquerade as
zero traffic. Google query rows are not used as property-wide totals.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

from pipeline.artifacts import atomic_write


def read_latest(directory: Path) -> dict:
    try:
        return json.loads((directory / "latest.json").read_text())
    except (OSError, ValueError):
        return {}


def post_catalog(posts_dir: Path, base_url: str) -> list[dict]:
    posts = []
    for path in sorted(posts_dir.glob("*.md")):
        source = path.read_text(encoding="utf-8")
        front = re.match(r"^---\s*\n(.*?)\n---", source, re.DOTALL)
        if not front:
            continue
        fields = {}
        for key in ("slug", "title", "pubDate"):
            match = re.search(rf"^{key}:\s*(.+)$", front[1], re.MULTILINE)
            fields[key] = match[1].strip().strip("\"'") if match else ""
        slug = fields["slug"] or path.stem
        posts.append({"slug": slug, "url": f"{base_url.rstrip('/')}/{slug}/",
                      "title": fields["title"], "date": fields["pubDate"][:10]})
    return posts


def collect_report(gsc, posts: list[dict], *, today: date,
                   previous_report: dict | None = None, inspect_limit: int = 20) -> dict:
    end = today - timedelta(days=3)
    start = end - timedelta(days=27)
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=27)

    def query(a, b, dimensions):
        return gsc.analytics(a.isoformat(), b.isoformat(), dimensions)

    def totals(rows):
        row = rows[0] if rows else {}
        return {k: row.get(k, 0) for k in ("clicks", "impressions", "ctr", "position")}

    current = totals(query(start, end, []))
    previous = totals(query(previous_start, previous_end, []))
    pages = query(start, end, ["page"])
    old_pages = {r["keys"][0]: r for r in query(previous_start, previous_end, ["page"])}
    query_pages = query(start, end, ["query", "page"])
    known_urls = {p["url"] for p in posts}
    inspections = {r["url"]: r for r in (previous_report or {}).get("indexing", [])
                   if r["url"] in known_urls}
    # Rotate by last inspection, so successful indexed URLs cannot starve others.
    queue = sorted(posts, key=lambda p: (
        inspections.get(p["url"], {}).get("checked_at", ""), p["date"], p["url"]))
    errors = []
    checked = 0
    for post in queue[:max(0, inspect_limit)]:
        try:
            result = gsc.inspect_url(post["url"])
            if not result.get("verdict"):
                raise ValueError("Google returned no index status")
            inspections[post["url"]] = {**post, **result, "checked_at": today.isoformat()}
            checked += 1
        except Exception as exc:
            errors.append({"url": post["url"], "error": str(exc)})
            # Stop after an API failure to avoid spending minutes per URL
            # against expired credentials/quota. Preserve earlier known states.
            break

    actions = []
    for row in pages:
        url = row["keys"][0]
        if url not in known_urls:
            continue
        old = old_pages.get(url, {})
        metrics = {k: row.get(k, 0) for k in ("clicks", "impressions", "ctr", "position")}
        if metrics["impressions"] >= 20 and 4 <= metrics["position"] <= 20:
            actions.append({"action": "improve_existing", "url": url, **metrics,
                            "reason": "Existing page has search visibility near page one; review intent, title and missing answers."})
        if old.get("clicks", 0) >= 5 and metrics["clicks"] <= old["clicks"] * 0.5:
            actions.append({"action": "investigate_decline", "url": url, **metrics,
                            "previous_clicks": old["clicks"],
                            "reason": "Clicks fell at least 50%; compare demand, ranking and recent changes before rewriting."})
    for row in inspections.values():
        if (row.get("verdict") != "PASS" and row.get("date")
                and row["date"] <= (today - timedelta(days=30)).isoformat()):
            actions.append({"action": "review_indexing", "url": row["url"],
                            "checked_at": row["checked_at"],
                            "reason": row.get("coverageState", "Not confirmed indexed")})

    overlaps = {}
    for row in query_pages:
        query_text, url = row["keys"]
        if url in known_urls and row.get("impressions", 0) >= 5:
            overlaps.setdefault(query_text, []).append({"url": url,
                "impressions": row.get("impressions", 0), "position": row.get("position")})
    return {
        "generated_on": today.isoformat(), "site_article_count": len(posts),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "previous_period": {"start": previous_start.isoformat(), "end": previous_end.isoformat()},
        "current": current, "previous": previous,
        "delta": {k: current[k] - previous[k] for k in ("clicks", "impressions")},
        "pages": pages, "query_pages": query_pages,
        "indexing": list(inspections.values()), "inspected_this_run": checked,
        "inspection_errors": errors, "actions": actions,
        "query_overlap_review": [{"query": q, "pages": rows}
                                  for q, rows in overlaps.items() if len(rows) > 1],
        "limitations": ["Only finalized Google web-search data; latest three days excluded.",
            "Search Console returns top query/page rows, not a complete export.",
            "Index status is a dated observation; rotating inspections are not a full live crawl.",
            "Shared queries are review candidates, not proof of harmful cannibalization.",
            "Search clicks do not measure leads or revenue."],
    }


def render_report(report: dict) -> str:
    cur, prev = report["current"], report["previous"]
    lines = [f"# SEO performance — {report['generated_on']}", "",
        f"Period: {report['period']['start']}–{report['period']['end']} (28 days).", "",
        "| Metric | Current | Previous 28 days |", "|---|---:|---:|",
        f"| Clicks | {cur['clicks']:g} | {prev['clicks']:g} |",
        f"| Impressions | {cur['impressions']:g} | {prev['impressions']:g} |",
        f"| CTR | {cur['ctr']:.2%} | {prev['ctr']:.2%} |", "",
        f"Inspected now: {report['inspected_this_run']}; dated observations available: "
        f"{len(report['indexing'])}/{report['site_article_count']}; "
        f"inspection errors: {len(report['inspection_errors'])}.", "",
        "## Review queue", ""]
    for action in report["actions"]:
        lines.append(f"- **{action['action']}** {action['url']} — {action['reason']}")
    lines += ["", "## Limits", "", *[f"- {s}" for s in report["limitations"]]]
    return "\n".join(lines) + "\n"


def refresh_report(cfg, gsc, posts_dir: Path, *, today: date,
                   force: bool = False, inspect_limit: int = 20) -> dict:
    latest = read_latest(cfg.performance_dir)
    generated = latest.get("generated_on", "")
    if not force and generated:
        try:
            if 0 <= (today - date.fromisoformat(generated)).days < 7:
                return latest
        except ValueError:
            pass
    report = collect_report(gsc, post_catalog(posts_dir, cfg.blog_base_url),
                            today=today, previous_report=latest, inspect_limit=inspect_limit)
    encoded = json.dumps(report, indent=2, ensure_ascii=False)
    atomic_write(cfg.performance_dir / f"{today.isoformat()}.json", encoded)
    atomic_write(cfg.performance_dir / f"{today.isoformat()}.md", render_report(report))
    atomic_write(cfg.performance_dir / "latest.json", encoded)
    return report


def planning_context(report: dict) -> str:
    if not report:
        return "No performance report available; do not infer zero traffic."
    return json.dumps({"generated_on": report["generated_on"],
        "period": report["period"], "current": report["current"],
        "existing_page_actions": report["actions"][:20],
        "query_overlap_review": report["query_overlap_review"][:10],
        "instruction": "These URLs already serve real demand. Do not create substitute articles for them; select a distinct unanswered question. Improvement actions belong to the existing URL."}, ensure_ascii=False)

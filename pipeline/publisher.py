"""Step 7 — Publisher (deterministic code).

Commits the post to the blog repo (push unless dry-run) and updates the
persistent stores. Idempotent: a same-day published status makes this a
no-op. Dry-run commits locally, never pushes, and does NOT mutate the
persistent stores (so dedupe state stays clean for the real run).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from clients import indexnow
from pipeline.dedupe import kw_tokens as _kw_tokens, near_duplicate as _kw_near_duplicate
from clients.retry import with_backoff


def _append_json_list(path: Path, key: str, entry: dict) -> dict:
    """Append ``entry`` to the list under ``key``, persist, return the data."""
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault(key, []).append(entry)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return data


def _norm_score(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _reconcile_keyword_backlog(path: Path, *, candidates: list, surplus: list,
                               published_keyword: str, published_set: set,
                               floor: float, cap: int, run_date: str) -> dict:
    """Fold today's non-selected candidates + surplus into the reserve, then
    prune it: drop already-published and duplicates, drop score < floor, keep
    the top `cap` by score. Self-maintaining — runs after every real publish."""
    data = json.loads(path.read_text(encoding="utf-8"))
    pub_tokens = _kw_tokens(published_keyword)

    entries: dict[str, dict] = {}

    def absorb(item: dict, *, default_date: str) -> bool:
        """Fold one keyword into the reserve. Returns True if it is genuinely
        new; a near-duplicate only lifts the score of the entry it matches."""
        kw = (item.get("keyword") or "").strip()
        if not kw:
            return False
        toks = _kw_tokens(kw)
        key = " ".join(sorted(toks)) or kw.lower()
        score = _norm_score(item.get("score"))
        held = entries.get(key)
        if held is None:
            for other in entries.values():
                if _kw_near_duplicate(toks, other["_tokens"]):
                    other["score"] = max(other["score"], score)
                    return False
            entries[key] = {"keyword": kw, "score": score,
                            "date": item.get("date") or default_date,
                            "_tokens": toks}
            return True
        held["score"] = max(held["score"], score)
        return False

    for e in data.get("candidates", []):
        absorb(e, default_date=e.get("date") or run_date)

    added = 0
    incoming = [c for c in candidates
                if not _kw_near_duplicate(_kw_tokens(c.get("keyword")),
                                          pub_tokens)] + list(surplus)
    for item in incoming:
        if absorb(item, default_date=run_date):
            added += 1

    published_tokens = [t for t in (_kw_tokens(k) for k in published_set) if t]
    kept = [e for e in entries.values()
            if e["score"] >= floor
            and not any(_kw_near_duplicate(e["_tokens"], p)
                        for p in published_tokens)]
    kept.sort(key=lambda e: e["score"], reverse=True)
    pruned = len(entries) - len(kept[:cap])

    data["candidates"] = [{k: v for k, v in e.items() if k != "_tokens"}
                          for e in kept[:cap]]
    data["updated"] = run_date
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return {"added": added, "pruned": pruned, "kept": len(data["candidates"])}


def _mark_content_map(path: Path, cluster: str) -> None:
    """Best-effort: tick the first open `[ ]` line that mentions the
    cluster keyword(s)."""
    if not cluster:
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    words = [w for w in re.split(r"[^a-z0-9]+", cluster.lower()) if len(w) > 3]
    for i, line in enumerate(lines):
        if line.strip().startswith("- [ ]") and any(
            w in line.lower() for w in words
        ):
            lines[i] = line.replace("- [ ]", "- [x]", 1)
            break
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def publish(*, cfg, assembled, brief: dict, topic: dict, stage: int,
            run_date: str, dry_run: bool, git_client, logger,
            candidates: list | None = None, surplus: list | None = None,
            published_keyword: str = "") -> dict:
    slug = assembled.slug
    file_name = f"{run_date}-{slug}.md"
    rel_path = f"{cfg.blog_posts_dir.rstrip('/')}/{file_name}"
    url = f"{cfg.blog_base_url.rstrip('/')}/{slug}"
    now = datetime.now(timezone.utc).isoformat()

    git_client.ensure_clone()
    git_client.write_post(rel_path, assembled.markdown)

    # Regenerate llms.txt (#17) and commit it alongside the post so AI search
    # engines get an up-to-date site map. Best-effort: a render failure must
    # never block the publish.
    commit_paths = [rel_path]
    try:
        from pipeline import llms
        il = json.loads((cfg.themes_dir / "internal_links.json")
                        .read_text(encoding="utf-8"))
        th = json.loads((cfg.backlog_dir / "topic_history.json")
                        .read_text(encoding="utf-8"))
        llms_content = llms.render(
            site_name=cfg.site_name, author_name=cfg.author_name,
            base_url=cfg.blog_base_url, internal_links=il, topic_history=th,
            new_post={"title": brief.get("title") or topic.get("topic") or slug,
                      "url": url, "slug": slug, "date": run_date})
        llms_path = cfg.blog_llms_path.strip() or "public/llms.txt"
        git_client.write_post(llms_path, llms_content)
        commit_paths.append(llms_path)
    except Exception as e:  # noqa: BLE001 — never block a publish
        logger.warning("llms.txt generation skipped: %s", e)

    commit_msg = f"post: {brief.get('title', topic.get('topic', slug))}"
    sha = git_client.commit_and_push(commit_paths, commit_msg,
                                     push=not dry_run)

    status = {
        "status": "dry_run" if dry_run else "published",
        "date": run_date,
        "slug": slug,
        "url": url,
        "file": rel_path,
        "commit": sha,
        "escalation_stage": stage,
        "timestamp": now,
    }

    if dry_run:
        logger.info("dry-run: committed locally, no push, stores untouched")
        return status

    # Real publish — mutate persistent stores.
    th = _append_json_list(
        cfg.backlog_dir / "topic_history.json", "published",
        {"topic": topic.get("topic"),
         "keyword": brief.get("primary_keyword"),
         "slug": slug, "url": url, "date": run_date,
         "escalation_stage": stage},
    )
    cluster = topic.get("cluster") or ""
    _append_json_list(
        cfg.themes_dir / "internal_links.json", "posts",
        {"cluster": cluster, "url": url, "slug": slug,
         "title": brief.get("title"), "date": run_date},
    )
    try:
        _mark_content_map(cfg.themes_dir / "content_map.md", cluster)
    except OSError as e:
        logger.warning("content_map update skipped: %s", e)

    published_set = {(p.get("keyword") or "").strip().lower()
                     for p in th.get("published", []) if p.get("keyword")}
    stats = _reconcile_keyword_backlog(
        cfg.backlog_dir / "keyword_backlog.json",
        candidates=candidates or [], surplus=surplus or [],
        published_keyword=published_keyword or brief.get("primary_keyword", ""),
        published_set=published_set, floor=cfg.backlog_score_floor,
        cap=cfg.backlog_max_size, run_date=run_date)
    status["backlog"] = stats
    logger.info("keyword_backlog: +%d, pruned %d, kept %d",
                stats["added"], stats["pruned"], stats["kept"])

    logger.info("published %s -> %s (commit %s)", slug, url, sha)

    # IndexNow ping — best-effort. The post is already pushed/published; a
    # failed indexation ping must never undo that, so swallow every error.
    if getattr(cfg, "indexnow_key", ""):
        page_url = url if url.endswith("/") else url + "/"
        try:
            with_backoff(
                lambda: indexnow.submit_url(
                    cfg.indexnow_site_url, page_url,
                    key=cfg.indexnow_key, endpoint=cfg.indexnow_endpoint),
                attempts=3, logger=logger, label="indexnow")
            logger.info("indexnow: submitted %s", page_url)
            status["indexnow_submitted"] = page_url
        except Exception as e:  # noqa: BLE001 — non-fatal by design
            logger.warning("indexnow submit failed (post stays published): %s", e)

    return status

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
    pub_kw = published_keyword.strip().lower()

    entries: dict[str, dict] = {}
    for e in data.get("candidates", []):
        kw = (e.get("keyword") or "").strip()
        if kw:
            entries[kw.lower()] = {"keyword": kw, "score": _norm_score(e.get("score")),
                                   "date": e.get("date") or run_date}

    incoming = [c for c in candidates
                if (c.get("keyword") or "").strip().lower() != pub_kw] + list(surplus)
    added = 0
    for item in incoming:
        kw = (item.get("keyword") or "").strip()
        if not kw:
            continue
        key = kw.lower()
        score = _norm_score(item.get("score"))
        if key in entries:
            entries[key]["score"] = max(entries[key]["score"], score)
        else:
            entries[key] = {"keyword": kw, "score": score, "date": run_date}
            added += 1

    kept = [e for key, e in entries.items()
            if key not in published_set and e["score"] >= floor]
    kept.sort(key=lambda e: e["score"], reverse=True)
    pruned = len(entries) - len(kept[:cap])

    data["candidates"] = kept[:cap]
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
    commit_msg = f"post: {brief.get('title', topic.get('topic', slug))}"
    sha = git_client.commit_and_push([rel_path], commit_msg,
                                     push=not dry_run)

    status = {
        "status": "dry_run" if dry_run else "published",
        "date": run_date,
        "slug": slug,
        "url": url,
        "file": rel_path,
        "commit": sha,
        "escalation_stage": stage,
        "confidential_leak_scrubbed": bool(assembled.leaked),
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

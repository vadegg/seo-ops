"""Step 7 — Publisher (deterministic code).

Commits the post to the blog repo (push unless dry-run) and updates the
persistent stores. Idempotent: a same-day published status makes this a
no-op. Dry-run commits locally, never pushes, and does NOT mutate the
persistent stores (so dedupe state stays clean for the real run).
"""

from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from clients import indexnow
from pipeline.dedupe import kw_tokens as _kw_tokens, near_duplicate as _kw_near_duplicate
from clients.retry import with_backoff
from pipeline.artifacts import ArtifactStore, PUBLISHER, atomic_write


def _append_json_list(path: Path, key: str, entry: dict) -> dict:
    """Append ``entry`` to the list under ``key``, persist, return the data."""
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.setdefault(key, [])
    if entry.get("slug"):
        entries[:] = [item for item in entries if item.get("slug") != entry["slug"]]
    entries.append(entry)
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))
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
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))
    return {"added": added, "pruned": pruned, "kept": len(data["candidates"])}


def _mark_content_map(path: Path, cluster: str) -> None:
    """Best-effort: tick the first open `[ ]` line that mentions the
    cluster keyword(s)."""
    if not cluster:
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    normalise = lambda value: " ".join(re.findall(r"[a-z0-9]+", value.lower()))
    for i, line in enumerate(lines):
        if line.strip().startswith("- [ ]") and normalise(line[5:]) == normalise(cluster):
            lines[i] = line.replace("- [ ]", "- [x]", 1)
            break
    atomic_write(path, "\n".join(lines) + "\n")


def publish(*, cfg, assembled, brief: dict, topic: dict, stage: int,
            run_date: str, dry_run: bool, git_client, logger,
            candidates: list | None = None, surplus: list | None = None,
            published_keyword: str = "", deployment_client=None) -> dict:
    store = ArtifactStore(cfg.runs_dir / run_date)
    previous = store.read_json(PUBLISHER) if store.exists(PUBLISHER) else {}
    if previous.get("status") == "published":
        return previous
    if previous.get("status") == "pushed" and dry_run:
        return previous

    slug = assembled.slug
    rel_path = f"{cfg.blog_posts_dir.rstrip('/')}/{run_date}-{slug}.md"
    url = f"{cfg.blog_base_url.rstrip('/')}/{slug}/"
    title = brief.get("title") or topic.get("topic") or slug
    content_hash = hashlib.sha256(assembled.markdown.encode()).hexdigest()

    if previous.get("status") == "pushed":
        status = previous
        # A pushed article is immutable during recovery: do not regenerate,
        # recommit, or accidentally verify a different slug.
        if (status.get("slug") != slug or status.get("title") != title
                or status.get("content_sha256", content_hash) != content_hash):
            raise RuntimeError("pending deployment belongs to a different article or content")
    else:
        git_client.ensure_clone()
        git_client.assert_unique_slug(rel_path, slug, cfg.blog_posts_dir)
        git_client.write_post(rel_path, assembled.markdown)
        git_client.validate()
        sha = git_client.commit_and_push([rel_path], f"post: {title}", push=not dry_run)
        status = {
            "status": "dry_run" if dry_run else "pushed", "date": run_date,
            "slug": slug, "url": url, "file": rel_path, "commit": sha,
            "title": title, "escalation_stage": stage,
            "content_sha256": content_hash,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        # Persist the commit before any optional service or state update.
        store.write_json(PUBLISHER, status)

    if dry_run:
        logger.info("dry-run: validated and committed locally; stores untouched")
        return status

    # Upserts make crash recovery safe even if one store was already written.
    th = _append_json_list(
        cfg.backlog_dir / "topic_history.json", "published",
        {"topic": topic.get("topic"), "keyword": brief.get("primary_keyword"),
         "slug": slug, "url": url, "date": run_date, "escalation_stage": stage})
    _append_json_list(
        cfg.themes_dir / "internal_links.json", "posts",
        {"cluster": topic.get("cluster") or "", "url": url, "slug": slug,
         "title": title, "date": run_date})
    _mark_content_map(cfg.themes_dir / "content_map.md", topic.get("cluster") or "")
    published_set = {(p.get("keyword") or "").strip().lower()
                     for p in th.get("published", []) if p.get("keyword")}
    status["backlog"] = _reconcile_keyword_backlog(
        cfg.backlog_dir / "keyword_backlog.json",
        candidates=candidates or [], surplus=surplus or [],
        published_keyword=published_keyword or brief.get("primary_keyword", ""),
        published_set=published_set, floor=cfg.backlog_score_floor,
        cap=cfg.backlog_max_size, run_date=run_date)
    store.write_json(PUBLISHER, status)

    if deployment_client is None:
        from clients.deployment import DeploymentClient
        deployment_client = DeploymentClient(timeout=cfg.deployment_timeout)
    status["deployment"] = deployment_client.wait_for_post(url, title)
    if not status["deployment"].get("verified"):
        raise RuntimeError("deployment verifier did not confirm the article")
    status["status"] = "published"
    status["published_at"] = datetime.now(timezone.utc).isoformat()
    store.write_json(PUBLISHER, status)
    logger.info("published and verified %s -> %s (commit %s)", slug, url, status["commit"])

    if getattr(cfg, "indexnow_key", ""):
        try:
            with_backoff(
                lambda: indexnow.submit_url(cfg.indexnow_site_url, url,
                    key=cfg.indexnow_key, endpoint=cfg.indexnow_endpoint),
                attempts=3, logger=logger, label="indexnow")
            status["indexnow_submitted"] = url
        except Exception as e:
            logger.warning("indexnow submit failed (article is live): %s", e)
    store.write_json(PUBLISHER, status)
    return status

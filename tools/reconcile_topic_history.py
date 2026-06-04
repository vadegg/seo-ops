"""Reconcile topic_history.json with what is actually live on the blog.

The researcher/strategist dedupe against topic_history's `published` list, so
it must contain every post on the site or the pipeline may re-propose covered
topics. Rebuilds the list from the blog repo's content collection (the source
of truth), preserving any existing per-entry fields (keyword, escalation_stage).

Usage: reconcile_topic_history.py <blog_content_dir> <topic_history.json> [base_url]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _scalar(block: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.*?)\s*$", block, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else ""


def _first_tag(block: str) -> str:
    m = re.search(r"^tags:\s*\n((?:[ \t]*-[ \t]*.*\n?)+)", block, re.MULTILINE)
    if not m:
        return ""
    t = re.search(r"-[ \t]*(.*?)\s*$", m.group(1), re.MULTILINE)
    return t.group(1).strip().strip('"').strip("'") if t else ""


def parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}
    block = m.group(1)
    return {
        "title": _scalar(block, "title"),
        "slug": _scalar(block, "slug"),
        "pubDate": _scalar(block, "pubDate"),
        "keyword": _first_tag(block),
    }


def main(content_dir: str, hist_file: str,
         base_url: str = "https://blog.glasgow.works/blog") -> None:
    hist_path = Path(hist_file)
    hist = json.loads(hist_path.read_text(encoding="utf-8"))
    by_slug = {e.get("slug"): dict(e) for e in hist.get("published", [])
               if e.get("slug")}

    added = 0
    for md in sorted(Path(content_dir).glob("*.md")):
        fm = parse_frontmatter(md.read_text(encoding="utf-8"))
        slug = fm.get("slug")
        if not slug:
            continue
        date = (fm.get("pubDate") or "")[:10]
        entry = by_slug.get(slug, {})
        if not entry:
            added += 1
        entry.setdefault("escalation_stage", 1)
        entry.update({
            "topic": fm.get("title") or entry.get("topic", ""),
            "keyword": entry.get("keyword") or fm.get("keyword", ""),
            "slug": slug,
            "url": f"{base_url.rstrip('/')}/{slug}/",
            "date": entry.get("date") or date,
        })
        by_slug[slug] = entry

    hist["published"] = sorted(by_slug.values(),
                               key=lambda e: (e.get("date") or "", e.get("slug")))
    hist_path.write_text(
        json.dumps(hist, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"reconciled: {len(hist['published'])} published "
          f"({added} newly added)")
    for e in hist["published"]:
        print(f"  {e['date']}  {e['slug']}")


if __name__ == "__main__":
    main(*sys.argv[1:])

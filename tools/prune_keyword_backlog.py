"""Re-prune backlog/keyword_backlog.json with the current dedupe rules.

The reserve accumulated rephrased variants of already-published keywords back
when pruning compared raw strings (see pipeline/publisher.py). Those entries
sat at the top by score and were re-proposed every day, which is how one topic
shipped eight times between 03.08 and 21.08.2026. Publishing re-prunes the
reserve, but only after the fact — run this once to clean what is already on
disk, and any time the reserve looks stale.

    python3 tools/prune_keyword_backlog.py [backlog_dir] [--apply]

Without --apply it only reports what would be dropped.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.dedupe import kw_tokens as _kw_tokens  # noqa: E402
from pipeline.dedupe import near_duplicate as _kw_near_duplicate  # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply = "--apply" in sys.argv[1:]
    backlog_dir = Path(args[0]) if args else Path("backlog")

    bl_path = backlog_dir / "keyword_backlog.json"
    data = json.loads(bl_path.read_text(encoding="utf-8"))
    history = json.loads((backlog_dir / "topic_history.json")
                         .read_text(encoding="utf-8"))
    published = [t for t in (_kw_tokens(p.get("keyword"))
                             for p in history.get("published", [])) if t]

    kept: list[dict] = []
    kept_tokens: list[frozenset] = []
    for entry in sorted(data.get("candidates", []),
                        key=lambda e: e.get("score", 0), reverse=True):
        toks = _kw_tokens(entry.get("keyword"))
        dup = next((p for p in published if _kw_near_duplicate(toks, p)), None)
        if dup:
            print(f"drop (published) {entry['score']:.2f}  {entry['keyword']}")
            continue
        if any(_kw_near_duplicate(toks, k) for k in kept_tokens):
            print(f"drop (in-reserve) {entry['score']:.2f}  {entry['keyword']}")
            continue
        kept.append(entry)
        kept_tokens.append(toks)

    print(f"\n{len(data.get('candidates', []))} -> {len(kept)} candidates")
    if apply:
        data["candidates"] = kept
        bl_path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                           encoding="utf-8")
        print(f"written: {bl_path}")
    else:
        print("dry run — pass --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

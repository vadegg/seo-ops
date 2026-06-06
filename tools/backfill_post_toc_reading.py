"""Backfill ToC and correct ``readingTime`` into already-published blog posts,
and drop the legacy in-body ``*Reading time:*`` line (#32/#33/#34).

Reuses the Assembler's enrichment (`_toc_block`, `_reading_time`) so backfilled
posts match what new posts get. Idempotent: re-running strips the ToC it added,
recomputes, and re-emits the identical result.

Run from the repo root so ``pipeline`` is importable:

    .venv/bin/python tools/backfill_post_toc_reading.py <blog_content_dir>
    .venv/bin/python tools/backfill_post_toc_reading.py path/to/post.md [...]
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.assembler import _reading_time, _toc_block  # noqa: E402

_FRONT = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_READING_LINE = re.compile(r"^\*Reading time:.*$\n?", re.MULTILINE)
_TOC_BLOCK = re.compile(
    r"<!-- gr:toc -->\s*\n\s*## On this page\s*\n+(?:[ \t]*-[^\n]*\n)+")


def _strip_toc(body: str) -> str:
    body = _READING_LINE.sub("", body)
    body = _TOC_BLOCK.sub("", body)
    return body.lstrip("\n")


def _set_reading_time(fm: str, value: int) -> str:
    line = f"readingTime: {value}"
    if re.search(r"^readingTime:.*$", fm, re.MULTILINE):
        return re.sub(r"^readingTime:.*$", line, fm, flags=re.MULTILINE)
    if re.search(r"^pubDate:.*$", fm, re.MULTILINE):
        return re.sub(r"^(pubDate:.*)$", r"\1\n" + line, fm, count=1,
                      flags=re.MULTILINE)
    return fm.rstrip() + "\n" + line


def process(text: str) -> tuple[str, str]:
    m = _FRONT.match(text)
    if not m:
        return text, "skipped (no frontmatter)"
    fm, body = m.group(1), text[m.end():]
    clean = _strip_toc(body)
    toc = _toc_block(clean)  # "" unless long enough with >=2 H2s
    new_body = (toc + "\n" + clean.strip() + "\n") if toc else clean.strip() + "\n"
    rt = _reading_time(new_body)
    new_fm = _set_reading_time(fm, rt)
    return f"---\n{new_fm}\n---\n{new_body}", f"readingTime={rt} toc={'yes' if toc else 'no'}"


def _iter_files(args: list[str]):
    for a in args:
        p = Path(a)
        if p.is_dir():
            yield from sorted(p.rglob("*.md"))
        else:
            yield p


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    changed = 0
    for path in _iter_files(argv):
        original = path.read_text(encoding="utf-8")
        new, note = process(original)
        status = "unchanged"
        if new != original:
            path.write_text(new, encoding="utf-8")
            changed += 1
            status = "updated"
        print(f"{status:9} {path.name}  ({note})")
    print(f"\n{changed} file(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

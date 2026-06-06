"""Demo (#39): show the Humanizer's DETERMINISTIC cliché strip on a sample
post — no API needed. Prints a before/after diff, the count of clichés
removed, and confirms every Markdown internal link survived.

The LLM rewrite needs the API; it is exercised in tests via the fake runner
(``tests/test_humanizer.py``). This demo only runs the deterministic layer.

Usage:
    python tools/demo_humanizer.py [path/to/post.md]

With no argument it uses a built-in sample full of clichés + an internal
link. Pass a Markdown file to humanise your own text instead.
"""

from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agents.humanizer import AI_CLICHES, strip_cliches  # noqa: E402

SAMPLE = """\
## Why sample size matters

In today's fast-paced world, research moves quickly. Moreover, the five-user rule is widely cited across the industry. It's important to note that 5 users find roughly 85% of usability problems. Furthermore, you should read our [research methods](/blog/ux-research-methods) hub for the full picture.

Additionally, task coverage matters more than raw participant count. In conclusion, plan for 5-8 participants per round and adjust as you learn.
"""

_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")


def _count_cliches(text: str) -> int:
    low = text.lower()
    return sum(low.count(c.lower()) for c in AI_CLICHES)


def main(argv: list[str]) -> int:
    if argv:
        before = Path(argv[0]).read_text(encoding="utf-8")
    else:
        before = SAMPLE

    after = strip_cliches(before)

    before_links = _LINK_RE.findall(before)
    after_links = _LINK_RE.findall(after)
    before_n = _count_cliches(before)
    after_n = _count_cliches(after)

    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="before", tofile="after", lineterm="")

    print("=== diff (deterministic cliché strip) ===")
    sys.stdout.writelines(diff)
    print("\n=== summary ===")
    print(f"clichés before:        {before_n}")
    print(f"clichés after:         {after_n}")
    print(f"clichés removed:       {before_n - after_n}")
    print(f"internal/MD links in:  {len(before_links)}")
    print(f"internal/MD links out: {len(after_links)}")
    survived = before_links == after_links
    print(f"links preserved:       {'YES' if survived else 'NO'}")
    if not survived:
        print("  ! link set changed — investigate", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

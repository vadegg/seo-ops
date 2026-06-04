"""One-off repair: bring already-published blog posts into line with the
Astro content schema (author/authorSlug/category + 150–160 char description).

Reuses the assembler's description-fitting so the result matches what new
posts produce. Idempotent: skips fields that are already present.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.assembler import (  # noqa: E402
    META_DESCRIPTION_MIN_LENGTH, _fit_meta_description)

AUTHOR_NAME = "Vadim Glazkov"
AUTHOR_SLUG = "vadim"
CATEGORY = "Research"


def fix(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?\n)---\n(.*)$", text, re.DOTALL)
    if not m:
        raise SystemExit(f"{path}: no frontmatter block found")
    fm_lines = m.group(1).rstrip("\n").splitlines()
    body = m.group(2)

    has = lambda k: any(l.startswith(f"{k}:") for l in fm_lines)  # noqa: E731
    out: list[str] = []
    changed: list[str] = []
    for line in fm_lines:
        if line.startswith("description:"):
            mm = re.match(r'description:\s*"(.*)"\s*$', line)
            raw = mm.group(1) if mm else line.split(":", 1)[1].strip().strip('"')
            fitted = _fit_meta_description(raw)
            if len(fitted) < META_DESCRIPTION_MIN_LENGTH:
                raise SystemExit(
                    f"{path}: description too short to fit ({len(fitted)})")
            if fitted != raw:
                changed.append(f"description {len(raw)}->{len(fitted)}")
            line = f'description: "{fitted}"'
        out.append(line)
        if line.startswith("slug:"):
            if not has("author"):
                out.append(f'author: "{AUTHOR_NAME}"'); changed.append("author")
            if not has("authorSlug"):
                out.append(f'authorSlug: "{AUTHOR_SLUG}"')
                changed.append("authorSlug")
            if not has("category"):
                out.append(f'category: "{CATEGORY}"'); changed.append("category")

    path.write_text("---\n" + "\n".join(out) + "\n---\n" + body,
                    encoding="utf-8")
    return ", ".join(changed) or "no change"


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(f"{p}: {fix(Path(p))}")

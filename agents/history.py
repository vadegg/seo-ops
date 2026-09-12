"""Rendering of the "already published" dedupe list for agent prompts.

The Researcher and the Strategist both need to know what the blog has
already covered; that list *is* the dedupe guard. Anything missing from it
gets happily re-proposed.

``topic_history.json`` is append-only and ordered oldest-first, so the old
``json.dumps(topics)[:4000]`` truncation dropped the **newest** entries —
precisely the ones a stale keyword reserve keeps re-suggesting. That is how
one topic shipped eight times between 03.08 and 21.08.2026: by then the
history was 6 074 chars, the prompt showed the first ~67 entries, and
everything published after 15.07 was invisible to both agents.

So: render newest-first, drop the oldest when the budget runs out, say how
many were dropped, and show the published *keyword* next to the topic — a
rephrased keyword is the usual disguise a duplicate arrives in.
"""

from __future__ import annotations

DEFAULT_BUDGET = 4000
EMPTY = "(nothing published yet)"


def _line(entry: dict) -> str:
    topic = (entry.get("topic") or entry.get("keyword") or "").strip()
    if not topic:
        return ""
    keyword = (entry.get("keyword") or "").strip()
    date = (entry.get("date") or "").strip()
    line = f"- {date} · {topic}" if date else f"- {topic}"
    if keyword and keyword.lower() != topic.lower():
        line += f"  [keyword: {keyword}]"
    return line


def render_published(topic_history: dict | None, *,
                     budget: int = DEFAULT_BUDGET) -> str:
    """Newest-first plain-text list of published posts, capped at ``budget``
    characters. Truncation always sheds the oldest entries."""
    published = list((topic_history or {}).get("published") or [])
    lines: list[str] = []
    used = 0
    shown = 0
    for entry in reversed(published):
        line = _line(entry)
        if not line:
            continue
        if lines and used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
        shown += 1
    if not lines:
        return EMPTY
    omitted = len(published) - shown
    if omitted > 0:
        lines.append(f"- (+{omitted} older post(s) omitted — this blog has "
                     f"broad coverage, so treat close neighbours of anything "
                     f"above as likely already covered)")
    return "\n".join(lines)

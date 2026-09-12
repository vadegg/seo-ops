"""Keyword/topic identity: is this the same article we already published?

Split out of the Publisher so the *selection* side (orchestrator, right
after the Strategist) and the *storage* side (the reusable keyword reserve)
judge duplicates by exactly the same rule.

Why a token rule and not string equality: the reserve used to compare raw
lowercased keywords, so one covered topic could sit in it forever under a
slightly different phrasing ("… in user research" vs "… in user research
(data quality)") and be re-proposed every single day. That shipped one topic
eight times between 03.08 and 21.08.2026.

Why not the MinHash body check (``pipeline/uniqueness.py``): that compares
*prose*. Those eight posts were written from scratch each time and score
~0.01 against each other — same subject, no shared phrasing. Text similarity
catches paraphrase; only the topic/keyword catches a re-run of the same
brief.
"""

from __future__ import annotations

import re

_PARENS = re.compile(r"\([^)]*\)")
_NONWORD = re.compile(r"[^a-z0-9\s]+")
_FILLER = frozenset("""
a an the and or vs versus for of in on at to how what when why is are do
does your you it its with that this
""".split())

NEAR_DUP = 0.7          # Jaccard over normalized token sets
CONTAINED_MIN = 4       # tokens a set needs before containment counts

# Calibrated against the real archive (95 posts, 03.2026–08.2026): these
# values catch 11/11 of the days that shipped a topic the blog had already
# covered, and flag 7 other days — all of them narrow articles sitting under
# a broad pillar-hub keyword ("ux research methods", "customer research").
# The asymmetry is deliberate: a false flag costs one extra escalation pass
# and a WARN (the day still publishes), a missed duplicate costs a duplicate
# post. CONTAINED_MIN=4 rather than 3 keeps three-token hub keywords from
# swallowing every specific article beneath them.


def _singular(token: str) -> str:
    if len(token) > 3 and token.endswith("s") and not token.endswith(
            ("ss", "us", "is")):
        return token[:-1]
    return token


def kw_tokens(keyword: str) -> frozenset:
    """Normalized token set of a keyword — the identity used for dedupe."""
    s = _PARENS.sub(" ", (keyword or "").lower())
    s = _NONWORD.sub(" ", s)
    return frozenset(_singular(t) for t in s.split()
                     if t and t not in _FILLER)


def near_duplicate(a: frozenset, b: frozenset,
                   threshold: float = NEAR_DUP) -> bool:
    """True when two keywords address the same article.

    Two ways to qualify: high Jaccard overlap (a rephrasing), or one keyword
    fully containing the other (an already-covered keyword dressed up with
    extra modifiers — "UX research for AI agents" vs "UX research for AI
    agents: how to test agentic features"). Containment needs at least
    ``CONTAINED_MIN`` tokens on the shorter side so a generic two-word
    keyword cannot swallow a whole cluster."""
    if not a or not b:
        return False
    if len(a & b) / len(a | b) >= threshold:
        return True
    smaller, larger = (a, b) if len(a) <= len(b) else (b, a)
    return len(smaller) >= CONTAINED_MIN and smaller <= larger


def duplicate_of_published(topic: dict, topic_history: dict | None) -> dict | None:
    """The published entry ``topic`` repeats, or None.

    Both the topic title and the primary keyword are tried against both
    fields of every published entry: a duplicate usually arrives with a
    reworded title *and* a reworded keyword, and either one matching is
    enough to call it covered."""
    probes = [kw_tokens(topic.get("topic")),
              kw_tokens(topic.get("primary_keyword"))]
    probes = [p for p in probes if p]
    if not probes:
        return None
    for entry in (topic_history or {}).get("published", []):
        targets = [kw_tokens(entry.get("topic")),
                   kw_tokens(entry.get("keyword"))]
        for p in probes:
            for t in targets:
                if near_duplicate(p, t):
                    return entry
    return None

"""Step 5b — Humanizer: reduce AI markers in the edited text (#39).

LLM text carries tell-tale markers: template connectives ("Moreover",
"It's important to note"), canned openers ("In today's fast-paced
world"), uniform rhythm, impersonal voice. There is no step that
deliberately humanises the prose — this is it.

Two layers, both safe to run without the API:

* ``strip_cliches`` — a DETERMINISTIC stop-phrase filter. It deletes the
  cliché connectives/openers in ``AI_CLICHES`` (the single source of
  truth, mirrored into ``style_guide.md``) and re-capitalises the
  sentence left behind. It touches only those phrases, so facts,
  numbers, and Markdown links (``[text](url)``) pass through verbatim.
  It is idempotent: running it twice equals running it once.

* ``run`` — an LLM rewrite that mirrors the Editor's forced-final
  pattern (structured input -> Markdown out; it knows nothing about
  retries/model choice). It varies sentence length, adds the agency's
  first-person voice and concreteness, and is anchored to
  ``style_guide.md`` plus the cliché list. It is NON-BLOCKING and never
  raises: a runner failure passes the (already-publishable) edited draft
  through UNCHANGED; an empty reply falls back to the deterministic
  strip. Either way the run carries on.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------
# The cliché stop-list — single source of truth (documented in
# style_guide.md). Seeded generously: template connectives, canned openers,
# and impersonal filler that mark machine-written prose.
# --------------------------------------------------------------------------
AI_CLICHES: list[str] = [
    # canned openers
    "In today's fast-paced world",
    "In today's digital age",
    "In the modern world",
    "In an ever-evolving landscape",
    "In the world of",
    "When it comes to",
    "At the end of the day",
    # template connectives
    "Moreover",
    "Furthermore",
    "Additionally",
    "In addition",
    "However, it is worth noting that",
    "That being said",
    "Needless to say",
    "Last but not least",
    # impersonal filler / hedged authority
    "It's important to note that",
    "It is important to note that",
    "It's worth noting that",
    "It is worth noting that",
    "It should be noted that",
    "It is crucial to understand that",
    "One must consider that",
    # canned closers
    "In conclusion",
    "In summary",
    "To sum up",
    "All in all",
    "Ultimately",
    # hype the style guide already bans, but commonly LLM-emitted
    "In essence",
    "Simply put",
]


# Longest-first so "It is important to note that" wins over "It is".
_SORTED_CLICHES = sorted(AI_CLICHES, key=len, reverse=True)


def _strip_one(text: str, phrase: str) -> str:
    """Remove ``phrase`` where it acts as a connective/opener: at the start
    of a sentence, optionally followed by a comma, then re-capitalise the
    word that now begins the sentence. Leaves the rest untouched."""
    # Match the phrase at start-of-string or right after sentence punctuation
    # / a newline, with an optional trailing comma and surrounding spaces.
    pat = re.compile(
        r"(?P<lead>(?:^|(?<=[.!?])\s+|(?<=\n)))"
        + re.escape(phrase)
        + r"\s*,?\s*",
        re.IGNORECASE,
    )

    out = []
    i = 0
    for m in pat.finditer(text):
        out.append(text[i:m.start()])
        out.append(m.group("lead"))
        i = m.end()
        # Capitalise the next alphabetic character that begins the sentence.
        if i < len(text) and text[i].islower():
            out.append(text[i].upper())
            i += 1
    out.append(text[i:])
    return "".join(out)


def strip_cliches(text: str) -> str:
    """Deterministically remove cliché connectives/openers. Idempotent;
    preserves facts, numbers and Markdown links."""
    for phrase in _SORTED_CLICHES:
        text = _strip_one(text, phrase)
    # Tidy any double spaces a removal left mid-line (never touch newlines).
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


SYSTEM_PROMPT = """\
You are the Humanizer for Glasgow Research's SEO blog. Your only job is to
make an already-edited, factually-correct post read like a human
practitioner wrote it — not a language model.

Rewrite for:
- Varied sentence length and rhythm. Mix short, punchy sentences with
  longer ones. Break the uniform cadence that marks machine text.
- The agency's first-person voice: "we" for Glasgow Research's experience,
  "you" for the reader. Calm authority, practitioner to practitioner.
- Concreteness: prefer specific nouns and numbers over abstractions.
- Lively, earned transitions instead of template connectives.

BANNED phrases — never use these or close variants (template connectives,
canned openers/closers, impersonal filler):
Moreover; Furthermore; Additionally; In addition; It's important to note
that; It's worth noting that; In today's fast-paced world; In the world of;
When it comes to; In conclusion; In summary; To sum up; Ultimately; In
essence; Simply put; Last but not least; Needless to say.

HARD CONSTRAINTS (correctness — violating these is worse than any style win):
- PRESERVE every fact, statistic and number exactly.
- PRESERVE every Markdown link verbatim: [anchor](url) pairs must be
  unchanged in text and target. Do not add, drop, or re-target links.
- Keep the heading structure (## H2s) and overall meaning.
- British spelling. No hype, no emoji, no fabricated data.

Output ONLY the rewritten Markdown body. No frontmatter, no commentary
before or after."""


def run(runner, *, model: str, tools: list[str], max_tokens: int, logger,
        draft_md: str, style_guide: str, evidence_passages: list) -> str:
    """Humanise ``draft_md``. Deterministic strip first, then an LLM
    rewrite. NON-BLOCKING: on any failure or empty reply, return the
    deterministic result (never raises, never empty)."""
    deterministic = strip_cliches(draft_md)

    ev = "\n\n".join(
        f"[evidence:{e.get('file','?')}] {e.get('text','')}"
        for e in evidence_passages[:6]
    ) or "(no evidence retrieved — do not invent specifics)"

    user = f"""## Edited post (humanise; preserve all facts and links)
{deterministic[:18000]}

## style_guide.md (Glasgow Research voice — obey)
{style_guide[:3500]}

## Evidence (for grounding the first-person voice — never fabricate)
{ev[:5000]}

Rewrite the post now. Output ONLY the Markdown body."""

    try:
        out = runner.run(name="humanizer", system=SYSTEM_PROMPT, user=user,
                         model=model, tools=tools, max_tokens=max_tokens,
                         logger=logger)
    except Exception as e:  # noqa: BLE001 — humanizer must never break the run
        # Hard non-blocking guarantee: a dead model returns the input
        # UNCHANGED rather than risk any lossy local edit. The run carries
        # on with the editor's already-publishable body.
        if logger:
            logger.warning("humanizer LLM pass failed (%s) — passing the "
                           "edited draft through unchanged", e)
        return draft_md

    out = (out or "").strip()
    if not out:
        if logger:
            logger.warning("humanizer LLM returned empty — keeping "
                           "deterministic result")
        return deterministic
    return out

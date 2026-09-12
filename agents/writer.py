"""Step 4 — Writer agent: the draft body (Markdown, no frontmatter)."""

from __future__ import annotations

import json

SYSTEM_PROMPT = """\
You are the Writer for Glasgow Research's SEO blog. Write the full post
body in Markdown, following the brief exactly and the style guide
faithfully.

Hard rules:
- Output ONLY the article body in Markdown. No frontmatter, no H1
  (start at H2), no commentary before or after.
- Hit the brief's structure, word counts, and internal links (use the
  exact anchor/URL pairs given — do not invent links).
- Ground non-obvious claims in the supplied evidence, but use it as
  GENERALISED, AGGREGATED insight. NEVER reproduce client names, project
  names, verbatim confidential text, or anything that identifies a
  specific client. If evidence is sensitive, abstract it.
- British spelling. Evidence-led, plain, calm. No hype, no emoji, no
  fabricated statistics.
"""


def run(runner, *, model: str, tools: list[str], max_tokens: int, logger,
        brief: dict, style_guide: str, evidence_passages: list) -> str:
    has_evidence = bool(evidence_passages)
    ev = "\n\n".join(
        f"[evidence:{e.get('file','?')}] {e.get('text','')}"
        for e in evidence_passages[:8]
    ) or "(no evidence retrieved — write from domain expertise, do not fabricate data)"

    # Retrieval is a suggestion, not proof that a client case supports this topic.
    first_hand = (
        "- Include a first-hand, anonymised example ONLY if a supplied passage "
        "directly documents the relevant work and supports every claimed detail. "
        "Unrelated passages are not evidence. Otherwise omit the case or use a "
        "clearly labelled hypothetical example, never invented agency experience. "
        "NEVER name or otherwise identify the client."
        if has_evidence else
        "- Do NOT invent first-hand examples or case studies — there is no "
        "evidence to ground them.")

    user = f"""## Brief (follow exactly)
{json.dumps(brief, ensure_ascii=False)}

## style_guide.md (obey)
{style_guide[:4000]}

## First-hand experience
{first_hand}

## Evidence corpus passages (generalise — never quote client-identifying text)
{ev[:9000]}

Write the complete Markdown body now."""

    return runner.run(name="writer", system=SYSTEM_PROMPT, user=user,
                       model=model, tools=tools, max_tokens=max_tokens,
                       logger=logger).strip()

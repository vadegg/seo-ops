"""Step 3 — Outliner agent: the SEO content brief + interlinking plan."""

from __future__ import annotations

import json
from datetime import date

from .runner import run_json
from .validation import validate_outliner

SYSTEM_PROMPT = """\
You are the Outliner for Glasgow Research's SEO blog. Turn the chosen
topic into a precise, competitive content brief that the Writer can
execute without guessing.

Requirements:
- Search-intent-matched structure; cover the query better than page-1.
- Verify primary sources before drafting. Record exact public URLs, publishers,
  check dates, and short excerpts supporting the material claims. Never invent
  URLs, quotations, product features or test results.
- Plan a usable original deliverable (filled example, worksheet, decision
  matrix or reproducible procedure). Explain how its reader task differs from
  the closest existing article; another keyword is not another reader task.
- Plan internal links ONLY from the provided internal-links map (use
  real URLs from it; never invent URLs). When the map offers enough
  relevant targets, distribute AT LEAST the requested minimum across
  different sections — do not leave the post orphaned.
- Include one section that invites a first-hand, anonymised example
  (set "first_hand_example": true on it) so the Writer can ground the
  post in real agency experience when evidence supports it.
- The site template owns BlogPosting; do not plan inline JSON-LD.
- Per-section word-count targets that sum near the total.

Output ONE JSON object, no prose:
{
  "title": str,                 // <= 60 chars, primary keyword natural
  "slug": str,                  // kebab-case
  "meta_description": str,      // 80-200 chars, complete benefit-led sentence
  "primary_keyword": str,
  "secondary_keywords": [str],
  "target_word_count": int,     // 1100-1800 typical
  "jsonld_type": "BlogPosting",
  "sources": [{"url": str, "publisher": str, "checked_on": "YYYY-MM-DD",
               "claim": str, "supporting_excerpt": str}],
  "original_value": {"deliverable": str, "reader_task": str,
                     "closest_existing_url": str, "difference": str},
  "sections": [
    {"h2": str, "key_points": [str], "word_count": int,
     "internal_links": [{"anchor": str, "url": str}],
     "first_hand_example": bool}   // true on at most one section
  ],
  "faq": [{"q": str, "a_outline": str}],
  "hero_image_alt": str
}
"""


def run(runner, *, model: str, tools: list[str], max_tokens: int, logger,
        topic: dict, content_map: str, internal_links: dict,
        min_links: int = 0) -> dict:
    link_rule = (f"Plan AT LEAST {min_links} internal links from the map below "
                 f"(it has enough relevant targets)."
                 if min_links else
                 "Plan internal links from the map below where relevant.")
    user = f"""Today's source verification date: {date.today().isoformat()}.

## Chosen topic (from Strategist)
{json.dumps(topic, ensure_ascii=False)[:4000]}

## Internal links available (use ONLY these URLs — pre-filtered by relevance)
{link_rule}
{json.dumps(internal_links, ensure_ascii=False)[:6000]}

## content_map.md (for cluster context)
{content_map[:4000]}

Use the available live web tool to search and read primary sources, then study
the closest competing answer. Do not treat an uninspected search snippet as
evidence for a price, product capability or numerical claim.
Keep supporting excerpts short. If a detail cannot be verified, omit it.

Return the brief JSON object now."""

    data = run_json(runner, name="outliner", system=SYSTEM_PROMPT, user=user,
                    model=model, tools=tools, max_tokens=max_tokens,
                    logger=logger, validate=validate_outliner)
    data.setdefault("jsonld_type", "BlogPosting")
    data.setdefault("sections", [])
    data.setdefault("faq", [])
    return data

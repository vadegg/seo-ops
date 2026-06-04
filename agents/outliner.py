"""Step 3 — Outliner agent: the SEO content brief + interlinking plan."""

from __future__ import annotations

import json

from .runner import run_json
from .validation import validate_outliner

SYSTEM_PROMPT = """\
You are the Outliner for Glasgow Research's SEO blog. Turn the chosen
topic into a precise, competitive content brief that the Writer can
execute without guessing.

Requirements:
- Search-intent-matched structure; cover the query better than page-1.
- Plan internal links ONLY from the provided internal-links map (use
  real URLs from it; never invent URLs). When the map offers enough
  relevant targets, distribute AT LEAST the requested minimum across
  different sections — do not leave the post orphaned.
- Include one section that invites a first-hand, anonymised example
  (set "first_hand_example": true on it) so the Writer can ground the
  post in real agency experience when evidence supports it.
- Specify a JSON-LD type appropriate to the page (usually "BlogPosting";
  use "FAQPage" only if a genuine FAQ section exists).
- Per-section word-count targets that sum near the total.

Output ONE JSON object, no prose:
{
  "title": str,                 // <= 60 chars, primary keyword natural
  "slug": str,                  // kebab-case
  "meta_description": str,      // 140-160 chars, benefit-led
  "primary_keyword": str,
  "secondary_keywords": [str],
  "target_word_count": int,     // 1100-1800 typical
  "jsonld_type": "BlogPosting|FAQPage",
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
    user = f"""## Chosen topic (from Strategist)
{json.dumps(topic, ensure_ascii=False)[:4000]}

## Internal links available (use ONLY these URLs — pre-filtered by relevance)
{link_rule}
{json.dumps(internal_links, ensure_ascii=False)[:6000]}

## content_map.md (for cluster context)
{content_map[:4000]}

{"You MAY use WebSearch to study what currently ranks and find the content gap."
 if tools else "Reason from the inputs; no web search this stage."}

Return the brief JSON object now."""

    data = run_json(runner, name="outliner", system=SYSTEM_PROMPT, user=user,
                    model=model, tools=tools, max_tokens=max_tokens,
                    logger=logger, validate=validate_outliner)
    data.setdefault("jsonld_type", "BlogPosting")
    data.setdefault("sections", [])
    data.setdefault("faq", [])
    return data

"""Step 2 — Strategist agent.

Picks the single best topic for the day, scores it 0..1, and ties it to
a content-map cluster. Score < threshold triggers escalation.
"""

from __future__ import annotations

import json

from . import history
from .runner import run_json
from .validation import validate_strategist

SYSTEM_PROMPT = """\
You are the Content Strategist for Glasgow Research's SEO blog. From the
candidate list, choose exactly ONE topic to publish today that best
strengthens topical authority and has real ranking + intent value.

Scoring (0..1) must reflect: search/intent value, ranking feasibility,
fit to an under-built content-map cluster, and freshness vs published
history. Be calibrated and honest — a weak day should score low so the
orchestrator can escalate. Do not inflate.

A candidate that repeats or rephrases an already-published post is NOT a
fresh topic: score it low (<= 0.3) even if its keyword looks strong, and
name the post it duplicates in the rationale.

A [x] cluster means it has at least one article, not that every useful
question within it is exhausted. Choose a genuinely distinct unanswered
question within a relevant cluster when no [ ] item remains. Explain the
specific gap; do not penalise a topic solely because its broad cluster is [x].

Output ONE JSON object, no prose:
{
  "topic": str,
  "primary_keyword": str,
  "secondary_keywords": [str],
  "search_intent": "informational|commercial|navigational",
  "cluster": str,            // content-map cluster/subtopic this feeds
  "pillar_hub_slug": str,    // product-research, ux-research-methods,
                            // research-operations, insight-to-impact, product-discovery
  "angle": str,              // the specific take, 1 sentence
  "score": number,           // 0..1, calibrated
  "rationale": str
}
"""


def run(runner, *, model: str, tools: list[str], max_tokens: int, logger,
        candidates: dict, topic_history: dict, content_map: str,
        performance_context: str = "") -> dict:
    user = f"""## Candidates (ranked by Researcher)
{json.dumps(candidates.get('candidates', []), ensure_ascii=False)[:8000]}

## content_map.md (prefer open gaps; [x] means covered once, not exhaustive)
{content_map[:6000]}

## Already published (avoid duplication AND rephrasing) — newest first
{history.render_published(topic_history)}

## Performance of existing URLs — do not duplicate their intent
{performance_context}

Choose one topic and return the JSON object now."""

    data = run_json(runner, name="strategist", system=SYSTEM_PROMPT, user=user,
                    model=model, tools=tools, max_tokens=max_tokens,
                    logger=logger, validate=validate_strategist)
    try:
        data["score"] = float(data.get("score", 0.0))
    except (TypeError, ValueError):
        data["score"] = 0.0
    return data

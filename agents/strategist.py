"""Step 2 — Strategist agent.

Picks the single best topic for the day, scores it 0..1, and ties it to
a content-map cluster. Score < threshold triggers escalation.
"""

from __future__ import annotations

import json

from .runner import extract_json

SYSTEM_PROMPT = """\
You are the Content Strategist for Glasgow Research's SEO blog. From the
candidate list, choose exactly ONE topic to publish today that best
strengthens topical authority and has real ranking + intent value.

Scoring (0..1) must reflect: search/intent value, ranking feasibility,
fit to an under-built content-map cluster, and freshness vs published
history. Be calibrated and honest — a weak day should score low so the
orchestrator can escalate. Do not inflate.

Output ONE JSON object, no prose:
{
  "topic": str,
  "primary_keyword": str,
  "secondary_keywords": [str],
  "search_intent": "informational|commercial|navigational",
  "cluster": str,            // content-map cluster/subtopic this feeds
  "pillar_hub_slug": str,    // e.g. "ux-research-methods" or "" if none
  "angle": str,              // the specific take, 1 sentence
  "score": number,           // 0..1, calibrated
  "rationale": str
}
"""


def run(runner, *, model: str, tools: list[str], max_tokens: int, logger,
        candidates: dict, topic_history: dict, content_map: str) -> dict:
    user = f"""## Candidates (ranked by Researcher)
{json.dumps(candidates.get('candidates', []), ensure_ascii=False)[:8000]}

## content_map.md (pick a topic that fills an open [ ] subtopic)
{content_map[:6000]}

## Already published (avoid duplication)
{json.dumps([p.get('topic') or p.get('keyword') for p in topic_history.get('published', [])], ensure_ascii=False)[:4000]}

Choose one topic and return the JSON object now."""

    out = runner.run(name="strategist", system=SYSTEM_PROMPT, user=user,
                      model=model, tools=tools, max_tokens=max_tokens,
                      logger=logger)
    data = extract_json(out)
    try:
        data["score"] = float(data.get("score", 0.0))
    except (TypeError, ValueError):
        data["score"] = 0.0
    return data

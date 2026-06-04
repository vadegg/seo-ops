"""Step 1 — Researcher agent.

Produces ranked keyword candidates. Data is gathered deterministically
by the orchestrator (GSC / DataForSEO with retry, or web search at
escalation stages) and injected as context — the agent reasons, it does
not own the API calls.
"""

from __future__ import annotations

import json

from .runner import extract_json

SYSTEM_PROMPT = """\
You are the Researcher for Glasgow Research's SEO blog (a UX & market
research agency). Your job: turn the provided signals into a ranked list
of keyword/topic candidates with genuine ranking potential and intent fit.

Rules:
- Prefer striking-distance and low-competition, high-intent terms.
- Exclude anything already in topic_history (dedupe).
- Every candidate must be relevant to a UX/market-research audience.
- Be honest about weak candidates; do not pad the list.
- Score every candidate 0..1 on the same axes as the Strategist
  (search/intent value, ranking feasibility, freshness vs published
  history). Be calibrated — do not inflate. The score feeds the reusable
  keyword backlog, so a weak candidate must score low.

Output ONE JSON object, no prose:
{
  "candidates": [
    {"keyword": str, "intent": "informational|commercial|navigational",
     "rationale": str, "source": "gsc|dataforseo|websearch|backlog|seed",
     "est_difficulty": "low|medium|high", "score": number 0..1,
     "supporting_data": str}
  ],
  "backlog_surplus": [ {"keyword": str, "score": number 0..1} ]
}
"candidates" is ranked best-first (max 12). "backlog_surplus" is strong
extras to persist for future days (may be empty).
"""


def run(runner, *, model: str, tools: list[str], max_tokens: int, logger,
        stage_spec, backlog: dict, topic_history: dict,
        gsc_rows: list | None, dfs_metrics: list | None,
        seed_topics: str | None) -> dict:
    user = f"""Escalation stage {stage_spec.stage}: {stage_spec.approach}

## backlog/keyword_backlog.json (cheap reserve — consider first)
{json.dumps(backlog.get('candidates', []), ensure_ascii=False)[:6000]}

## Already published (DO NOT repeat) — topic_history
{json.dumps([p.get('topic') or p.get('keyword') for p in topic_history.get('published', [])], ensure_ascii=False)[:4000]}

## GSC near-top queries (may be empty / unavailable)
{json.dumps(gsc_rows or [], ensure_ascii=False)[:6000]}

## DataForSEO metrics (may be empty / unavailable)
{json.dumps(dfs_metrics or [], ensure_ascii=False)[:6000]}

## Evergreen seed list (use at stage 4, or for inspiration)
{(seed_topics or '')[:4000] if stage_spec.use_seed_list else '(not this stage)'}

{"You MAY use the WebSearch tool for SERP-gap and competitor analysis."
 if stage_spec.use_websearch else
 "Web search is NOT available this stage — reason from the data above."}

Return the JSON object now."""

    out = runner.run(name="researcher", system=SYSTEM_PROMPT, user=user,
                      model=model, tools=tools, max_tokens=max_tokens,
                      logger=logger)
    data = extract_json(out)
    data.setdefault("candidates", [])
    data.setdefault("backlog_surplus", [])
    for c in data["candidates"]:
        try:
            c["score"] = max(0.0, min(1.0, float(c.get("score", 0.0))))
        except (TypeError, ValueError):
            c["score"] = 0.0
    return data

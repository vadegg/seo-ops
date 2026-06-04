"""Step 5 — Editor / Critic agent: self-critique loop.

Returns the edited markdown plus a checklist critique. The orchestrator
runs this up to 2 iterations; if still failing it forces a final rewrite
on Opus and fires a hard alert — it never abandons the day.
"""

from __future__ import annotations

import json

from .runner import extract_json

SYSTEM_PROMPT = """\
You are the Editor/Critic for Glasgow Research's SEO blog. Improve the
draft and judge it against a strict checklist. You may rewrite freely to
pass the checklist; preserve the brief's intent and structure.

The checklist (all must be true to pass):
- on_brief: covers the brief's sections, intent, and word target
- style_guide: voice, British spelling, no hype/emoji, evidence-led
- seo: title<=60, meta 140-160, primary keyword used naturally, good H2s
- internal_links: only the brief's anchor/URL pairs, placed naturally
- evidence_grounded: non-obvious claims supported, no fabricated stats
- no_client_leak: NO client-identifying or confidential material; only
  generalised, aggregated insight (this one is non-negotiable)

Output ONE JSON object, no prose:
{
  "edited_markdown": str,        // the improved full body, Markdown
  "critique": {
    "checklist": {"on_brief": bool, "style_guide": bool, "seo": bool,
      "internal_links": bool, "evidence_grounded": bool,
      "no_client_leak": bool},
    "passed": bool,              // true only if every checklist item true
    "notes": str
  }
}
"""


def run(runner, *, model: str, tools: list[str], max_tokens: int, logger,
        draft_md: str, brief: dict, style_guide: str,
        evidence_passages: list, iteration: int, final: bool = False) -> dict:
    ev = "\n\n".join(
        f"[evidence:{e.get('file','?')}] {e.get('text','')}"
        for e in evidence_passages[:8]
    ) or "(no evidence retrieved)"

    push = ("This is the FINAL pass — you MUST return the best possible "
            "publishable version with passed=true achievable; fix every "
            "issue now." if final else
            f"Iteration {iteration}. If it does not pass, rewrite to pass.")

    user = f"""{push}

## Draft
{draft_md[:18000]}

## Brief
{json.dumps(brief, ensure_ascii=False)[:6000]}

## style_guide.md
{style_guide[:3500]}

## Evidence (verify claims are supported & not client-identifying)
{ev[:7000]}

Return the JSON object now."""

    out = runner.run(name="editor", system=SYSTEM_PROMPT, user=user,
                      model=model, tools=tools, max_tokens=max_tokens,
                      logger=logger)
    data = extract_json(out)
    crit = data.setdefault("critique", {})
    cl = crit.setdefault("checklist", {})
    crit["passed"] = bool(crit.get("passed")) and all(
        bool(v) for v in cl.values()
    ) if cl else False
    if not data.get("edited_markdown"):
        data["edited_markdown"] = draft_md
    return data

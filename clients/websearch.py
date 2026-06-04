"""Web search integration.

Web search is the Claude Agent SDK built-in tool. It is not a standalone
HTTP client: the orchestrator grants the ``WebSearch`` tool to the
Researcher/Strategist/Outliner agents on escalation stages 2–4, and the
agent decides when to call it. This module just centralises the tool
name so the wiring lives in one place.
"""

from __future__ import annotations

WEB_SEARCH_TOOL = "WebSearch"


def tools_for_stage(stage: int) -> list[str]:
    """Allowed agent tools by escalation stage.

    Stage 1 relies on injected GSC/DataForSEO context only (no web).
    Stages 2–4 add live web search for SERP-gap / competitor analysis.
    """
    return [] if stage <= 1 else [WEB_SEARCH_TOOL]

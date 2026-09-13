"""Web search integration.

``WebSearch`` enables live web access in the CLI runner. The Researcher
uses it according to its escalation stage. The Outliner always receives
it to verify sources after a topic has been selected, including stage 1.
"""

from __future__ import annotations

WEB_SEARCH_TOOL = "WebSearch"


def tools_for_stage(stage: int) -> list[str]:
    """Allowed agent tools by escalation stage.

    Stage 1 relies on injected GSC/DataForSEO context only (no web).
    Stages 2–4 add live web search for SERP-gap / competitor analysis.
    """
    return [] if stage <= 1 else [WEB_SEARCH_TOOL]

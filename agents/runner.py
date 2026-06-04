"""Isolation point for LLM calls.

Every agent goes through AgentRunner.run. Control flow, model selection and
artifact passing stay in the orchestrator, not in the agents.
"""

from __future__ import annotations

import json
import re
from typing import Protocol


class AgentRunner(Protocol):
    def run(self, *, name: str, system: str, user: str, model: str,
            tools: list[str], max_tokens: int, logger) -> str: ...


def extract_json(text: str):
    """Best-effort JSON extraction from an agent reply.

    Tolerates code fences and surrounding prose.
    """
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = text.find(open_c)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == open_c:
                depth += 1
            elif text[i] == close_c:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
    raise ValueError("agent did not return parseable JSON")


# Anthropic server-side web search tool. The `name` field is required by the
# API; the model runs the search server-side and returns results inline.
_WEB_SEARCH_API_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 5,
}

# Max model turns when web search is enabled (server tool may `pause_turn`).
_MAX_TOOL_TURNS = 8


class SDKAgentRunner:
    """Runner backed by the direct Anthropic Messages API."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    def run(self, *, name: str, system: str, user: str, model: str,
            tools: list[str], max_tokens: int, logger) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)

        # Map SDK tool names to Anthropic API tools
        api_tools = []
        if "WebSearch" in tools:
            api_tools.append(_WEB_SEARCH_API_TOOL)

        if logger:
            logger.info("agent %s -> model=%s tools=%s", name, model, tools)

        messages = [{"role": "user", "content": user}]
        turns = 0
        max_turns = _MAX_TOOL_TURNS if api_tools else 1
        text_chunks: list[str] = []

        while turns < max_turns:
            turns += 1
            kwargs = dict(
                model=model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
            )
            if api_tools:
                kwargs["tools"] = api_tools

            response = client.messages.create(**kwargs)

            # Collect text from this turn
            for block in response.content:
                if hasattr(block, "text"):
                    text_chunks.append(block.text)

            # Server-side web search runs inside one assistant turn and finishes
            # with `end_turn`; a long agentic search yields `pause_turn`, which
            # we continue by replaying the assistant content. No client-side
            # tool_result is needed (the search executes on Anthropic's side).
            if response.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": response.content})
                continue
            break

        out = "\n".join(text_chunks).strip()
        if logger:
            logger.info("agent %s produced %d chars", name, len(out))
        if not out:
            raise RuntimeError(f"agent {name} returned empty output")
        return out

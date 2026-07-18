"""Isolation point for LLM calls.

Every agent goes through AgentRunner.run. Control flow, model selection and
artifact passing stay in the orchestrator, not in the agents. The concrete
runner shells out to the ``claude`` CLI (Claude Code, headless ``-p`` mode) —
this module is the only place that knows the LLM backend.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
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


def run_json(runner, *, name: str, system: str, user: str, model: str,
             tools: list, max_tokens: int, logger, validate):
    """Call an agent, parse + validate its JSON, and re-prompt once on
    failure (#6). A second failure raises ``ClientError`` so the
    orchestrator's escalation/alerts engage instead of shipping a
    structurally-broken artifact.

    ``validate`` is a callable that raises ``ValueError`` (e.g.
    ``ValidationError``) when the parsed object breaks the agent contract.
    """
    from clients.retry import ClientError

    def _attempt(u: str):
        out = runner.run(name=name, system=system, user=u, model=model,
                         tools=tools, max_tokens=max_tokens, logger=logger)
        data = extract_json(out)
        validate(data)
        return data

    try:
        return _attempt(user)
    except ValueError as first:
        if logger:
            logger.warning("agent %s output rejected (%s) — re-prompting once",
                           name, first)
        retry_user = (f"{user}\n\n## Your previous reply was INVALID\n"
                      f"{first}\nReturn ONLY corrected JSON that matches the "
                      f"schema. No prose.")
        try:
            return _attempt(retry_user)
        except ValueError as second:
            raise ClientError(
                f"agent {name} returned invalid output twice: {second}"
            ) from second


def _extract_usage(name: str, model: str, data: dict) -> dict:
    """Fold the CLI's usage block into one accounting record.

    The ``claude`` CLI reports usage two ways: a top-level ``usage`` object
    and a per-model ``modelUsage`` map keyed by the resolved model id, which
    also carries ``costUSD`` (cache-aware, authoritative). Prefer ``modelUsage``
    — summing across entries — and fall back to ``usage`` + ``total_cost_usd``.
    The ``usd`` field lets ``pipeline.usage.summarize`` skip the local price
    table entirely (#5).
    """
    model_usage = data.get("modelUsage") or {}
    if model_usage:
        in_tok = sum(int(v.get("inputTokens", 0) or 0) for v in model_usage.values())
        out_tok = sum(int(v.get("outputTokens", 0) or 0) for v in model_usage.values())
        usd = sum(float(v.get("costUSD", 0.0) or 0.0) for v in model_usage.values())
        rec_model = next(iter(model_usage)) if len(model_usage) == 1 else model
    else:
        u = data.get("usage") or {}
        in_tok = int(u.get("input_tokens", 0) or 0)
        out_tok = int(u.get("output_tokens", 0) or 0)
        usd = float(data.get("total_cost_usd", 0.0) or 0.0)
        rec_model = model
    return {"agent": name, "model": rec_model,
            "input_tokens": in_tok, "output_tokens": out_tok,
            "usd": round(usd, 6)}


class CLIAgentRunner:
    """Runner backed by the ``claude`` CLI (Claude Code, headless ``-p`` mode).

    Each ``run`` is a stateless subprocess authenticated by the CLI's own
    logged-in **subscription** session (Claude Max/Team) — no API key.
    ``--system-prompt`` fully replaces Claude Code's default system prompt
    (bare-agent mode); ``--safe-mode`` disables CLAUDE.md/hooks/plugins/MCP for
    reproducible calls while keeping normal (subscription) auth. To guarantee
    the subscription is used, ``ANTHROPIC_API_KEY`` is stripped from the child
    environment so a stray key can't silently switch the run to API billing.
    Web search maps to the CLI's built-in ``WebSearch`` tool; with no tools the
    model just generates text. The CLI runs any agentic tool loop internally,
    so this returns one usage record per call.
    """

    def __init__(self, claude_bin: str = "claude", timeout: int = 600):
        self._bin = claude_bin
        self._timeout = timeout
        # One token/cost record per agent call, accumulated across the run (#5).
        self.records: list[dict] = []

    def run(self, *, name: str, system: str, user: str, model: str,
            tools: list[str], max_tokens: int, logger) -> str:
        from clients.retry import ClientError

        # max_tokens has no CLI equivalent (no --max-tokens flag); kept in the
        # signature for the AgentRunner contract but not enforced here.
        cmd = [
            self._bin, "-p", user,
            "--model", model,
            "--output-format", "json",
            "--system-prompt", system,
            "--safe-mode",
            "--no-session-persistence",
        ]
        if "WebSearch" in tools:
            # Built-in WebSearch is read-only; bypass keeps headless -p from
            # blocking on a permission prompt.
            cmd += ["--tools", "WebSearch", "--permission-mode", "bypassPermissions"]
        else:
            cmd += ["--tools", ""]

        # Force the CLI's subscription/OAuth login: drop any ambient API key so
        # it can't take precedence and bill per-token instead.
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)

        if logger:
            logger.info("agent %s -> model=%s tools=%s", name, model, tools)

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, env=env,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClientError(
                f"agent {name}: claude CLI timed out after {self._timeout}s") from exc
        except OSError as exc:  # binary missing / not executable
            raise ClientError(
                f"agent {name}: could not spawn claude CLI ({self._bin}): {exc}") from exc

        if proc.returncode != 0:
            raise ClientError(
                f"agent {name}: claude CLI exited {proc.returncode}: "
                f"{(proc.stderr or '').strip()[:500]}")

        try:
            data = json.loads(proc.stdout)
        except ValueError as exc:
            raise ClientError(
                f"agent {name}: claude CLI returned non-JSON output: "
                f"{(proc.stdout or '').strip()[:500]}") from exc

        if data.get("is_error") or data.get("subtype") != "success":
            raise ClientError(
                f"agent {name}: claude CLI reported failure "
                f"(subtype={data.get('subtype')}, api_error_status="
                f"{data.get('api_error_status')})")

        self.records.append(_extract_usage(name, model, data))

        out = (data.get("result") or "").strip()
        if logger:
            logger.info("agent %s produced %d chars", name, len(out))
        if not out:
            raise RuntimeError(f"agent {name} returned empty output")
        return out

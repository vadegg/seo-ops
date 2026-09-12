"""Isolation point for LLM calls.

Every agent goes through AgentRunner.run. Control flow, model selection and
artifact passing stay in the orchestrator, not in the agents. The concrete
runner shells out to the ``codex`` CLI (non-interactive ``exec`` mode) —
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


class CLIAgentRunner:
    """Runner backed by ``codex exec`` and the user's ChatGPT/Codex login.

    Calls are ephemeral and ignore personal/project configuration. Local shell
    tools are disabled; the only optional capability is hosted web search.
    Ambient OpenAI API credentials are stripped so authentication can only come
    from the cached subscription login. Codex emits JSONL events, folded here
    into the existing text response and token accounting contract.
    """

    def __init__(self, codex_bin: str = "codex", timeout: int = 600,
                 attempts: int = 3):
        self._bin = codex_bin
        self._timeout = timeout
        self._attempts = attempts
        # One token/cost record per agent call, accumulated across the run (#5).
        self.records: list[dict] = []

    def run(self, *, name: str, system: str, user: str, model: str,
            tools: list[str], max_tokens: int, logger) -> str:
        from clients.retry import with_backoff

        return with_backoff(
            lambda: self._run_once(name=name, system=system, user=user,
                                  model=model, tools=tools,
                                  max_tokens=max_tokens, logger=logger),
            attempts=self._attempts, logger=logger, label=f"agent {name}")

    def _run_once(self, *, name: str, system: str, user: str, model: str,
                  tools: list[str], max_tokens: int, logger) -> str:
        from clients.retry import ClientError, PermanentClientError

        # max_tokens has no CLI equivalent (no --max-tokens flag); kept in the
        # signature for the AgentRunner contract but not enforced here.
        unsupported = sorted(set(tools) - {"WebSearch"})
        if unsupported:
            raise PermanentClientError(f"agent {name}: unsupported Codex tools: {', '.join(unsupported)}")
        cmd = [
            self._bin, "exec", "--model", model, "--ephemeral",
            "--skip-git-repo-check", "--sandbox", "read-only",
            "--ignore-user-config", "--ignore-rules",
            "--disable", "apps", "--disable", "plugins",
            "--disable", "multi_agent", "--disable", "browser_use",
            "--disable", "computer_use", "--disable", "image_generation",
            "--disable", "shell_tool", "--disable", "unified_exec",
            "--config",
            f'web_search={json.dumps("live" if "WebSearch" in tools else "disabled")}',
            "--json", "-",
        ]

        env = os.environ.copy()
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
            env.pop(key, None)

        if logger:
            logger.info("agent %s -> model=%s tools=%s", name, model, tools)

        try:
            proc = subprocess.run(
                cmd, input=f"{system}\n\n---\n\n{user}",
                capture_output=True, text=True, env=env,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClientError(
                f"agent {name}: Codex CLI timed out after {self._timeout}s") from exc
        except OSError as exc:  # binary missing / not executable
            raise PermanentClientError(
                f"agent {name}: could not spawn Codex CLI ({self._bin}): {exc}") from exc

        events = []
        invalid_lines = []
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    events.append(event)
                else:
                    invalid_lines.append(line)
            except ValueError:
                invalid_lines.append(line)
        failed = next((event for event in events if event.get("type") == "turn.failed"), None)
        errors = [event.get("message", "") for event in events
                  if event.get("type") == "error"]
        if proc.returncode != 0 or failed:
            reason = ((failed or {}).get("error") or {}).get("message", "")
            reason = reason or "; ".join(filter(None, errors))
            reason = reason or (proc.stderr or "").strip() or "no error details from CLI"
            permanent = any(s in reason.lower() for s in (
                "not logged in", "authentication", "refresh_token", "usage limit",
                "quota exceeded", "invalid api key", "unsupported model",
                "model is not supported", "unknown model", "unrecognized argument"))
            error_type = PermanentClientError if permanent else ClientError
            raise error_type(f"agent {name}: Codex CLI exited {proc.returncode}: {reason[:1500]}")
        if invalid_lines:
            raise ClientError(
                f"agent {name}: Codex CLI returned non-JSONL output: "
                f"{invalid_lines[0][:500]}")
        completed = next((event for event in reversed(events) if event.get("type") == "turn.completed"), None)
        if failed or not completed:
            reason = (failed or {}).get("error", {}).get("message", "turn did not complete")
            raise ClientError(
                f"agent {name}: Codex CLI reported failure ({reason})")

        usage = completed.get("usage") or {}
        self.records.append({
            "agent": name, "model": model,
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "usd": 0.0,
        })
        messages = [
            event.get("item", {}).get("text", "")
            for event in events
            if event.get("type") == "item.completed"
            and event.get("item", {}).get("type") == "agent_message"
        ]
        out = (messages[-1] if messages else "").strip()
        if logger:
            logger.info("agent %s produced %d chars", name, len(out))
        if not out:
            raise ClientError(f"agent {name} returned empty output")
        return out

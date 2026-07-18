"""CLIAgentRunner: the `claude`-CLI-backed agent runner.

No real subprocess — ``subprocess.run`` is monkeypatched to return canned CLI
JSON, so these assert on argv construction, usage/cost extraction, and error
handling in isolation.
"""

import json
import subprocess

import pytest

from agents import runner as runner_mod
from agents.runner import CLIAgentRunner
from clients.retry import ClientError


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)


def _cli_json(result="ok", model="claude-sonnet-5", usd=0.0123,
              in_tok=1200, out_tok=450, subtype="success", is_error=False):
    return json.dumps({
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "api_error_status": None,
        "result": result,
        "stop_reason": "end_turn",
        "total_cost_usd": usd,
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        "modelUsage": {model: {"inputTokens": in_tok, "outputTokens": out_tok,
                               "cacheReadInputTokens": 0,
                               "cacheCreationInputTokens": 0,
                               "webSearchRequests": 0, "costUSD": usd}},
    })


def _patch(monkeypatch, *, stdout=None, returncode=0, stderr="", capture=None):
    if stdout is None:
        stdout = _cli_json()

    def fake_run(cmd, **kwargs):
        if capture is not None:
            capture["cmd"] = cmd
            capture["kwargs"] = kwargs
        return _completed(stdout=stdout, returncode=returncode, stderr=stderr)

    monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)


def _run(runner, *, tools=(), model="claude-sonnet-5"):
    return runner.run(name="writer", system="SYS", user="USER", model=model,
                      tools=list(tools), max_tokens=8000, logger=None)


# ---- happy path ------------------------------------------------------------
def test_returns_result_text(monkeypatch):
    _patch(monkeypatch, stdout=_cli_json(result="Hello world"))
    out = _run(CLIAgentRunner())
    assert out == "Hello world"


def test_records_carry_usd_and_resolved_model(monkeypatch):
    _patch(monkeypatch, stdout=_cli_json(
        model="claude-sonnet-5", usd=0.05, in_tok=100, out_tok=200))
    runner = CLIAgentRunner()
    _run(runner, model="sonnet")  # alias in, full id echoed back by CLI
    assert len(runner.records) == 1
    rec = runner.records[0]
    assert rec == {"agent": "writer", "model": "claude-sonnet-5",
                   "input_tokens": 100, "output_tokens": 200, "usd": 0.05}


def test_usage_falls_back_to_top_level_when_no_modelusage(monkeypatch):
    data = json.loads(_cli_json(in_tok=7, out_tok=9, usd=0.9))
    del data["modelUsage"]
    _patch(monkeypatch, stdout=json.dumps(data))
    runner = CLIAgentRunner()
    _run(runner, model="claude-opus-4-8")
    rec = runner.records[0]
    assert rec["input_tokens"] == 7 and rec["output_tokens"] == 9
    assert rec["usd"] == 0.9
    assert rec["model"] == "claude-opus-4-8"  # falls back to the requested id


# ---- argv construction -----------------------------------------------------
def test_argv_no_tools(monkeypatch):
    cap = {}
    _patch(monkeypatch, capture=cap)
    _run(CLIAgentRunner(claude_bin="/opt/claude"), tools=[])
    cmd = cap["cmd"]
    assert cmd[0] == "/opt/claude"
    assert cmd[1:3] == ["-p", "USER"]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "claude-sonnet-5"
    assert "--system-prompt" in cmd and cmd[cmd.index("--system-prompt") + 1] == "SYS"
    assert "--safe-mode" in cmd
    assert "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "json"
    # tools disabled, no permission bypass
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--permission-mode" not in cmd


def test_argv_websearch_gated(monkeypatch):
    cap = {}
    _patch(monkeypatch, capture=cap)
    _run(CLIAgentRunner(), tools=["WebSearch"])
    cmd = cap["cmd"]
    assert cmd[cmd.index("--tools") + 1] == "WebSearch"
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"


def test_api_key_stripped_from_env(monkeypatch):
    # Force subscription auth: any ambient API key must not reach the CLI, or it
    # would take precedence and bill per-token.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-dropped")
    cap = {}
    _patch(monkeypatch, capture=cap)
    _run(CLIAgentRunner())
    assert "ANTHROPIC_API_KEY" not in cap["kwargs"]["env"]


# ---- failures --------------------------------------------------------------
def test_nonzero_exit_raises(monkeypatch):
    _patch(monkeypatch, stdout="", returncode=1, stderr="boom")
    with pytest.raises(ClientError):
        _run(CLIAgentRunner())


def test_non_json_stdout_raises(monkeypatch):
    _patch(monkeypatch, stdout="not json at all")
    with pytest.raises(ClientError):
        _run(CLIAgentRunner())


def test_is_error_raises(monkeypatch):
    _patch(monkeypatch, stdout=_cli_json(is_error=True, subtype="error_during_execution"))
    with pytest.raises(ClientError):
        _run(CLIAgentRunner())


def test_empty_result_raises(monkeypatch):
    _patch(monkeypatch, stdout=_cli_json(result="   "))
    with pytest.raises(RuntimeError):
        _run(CLIAgentRunner())


def test_timeout_raises_clienterror(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 600)

    monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
    with pytest.raises(ClientError):
        _run(CLIAgentRunner())

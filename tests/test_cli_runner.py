"""CLIAgentRunner: the Codex-subscription CLI runner.

No real subprocess runs here. ``subprocess.run`` returns canned Codex JSONL
events so argv, isolation, usage accounting, and failures stay deterministic.
"""

import json
import subprocess

import pytest

from agents import runner as runner_mod
from agents.runner import CLIAgentRunner
from clients.retry import ClientError


@pytest.fixture(autouse=True)
def no_retry_delays(monkeypatch):
    monkeypatch.setattr("clients.retry.time.sleep", lambda _: None)


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["codex"], returncode=returncode, stdout=stdout, stderr=stderr)


def _codex_jsonl(result="ok", in_tok=1200, out_tok=450, *, failed=None):
    events = [
        {"type": "thread.started", "thread_id": "test"},
        {"type": "turn.started"},
    ]
    if failed is not None:
        events.append({"type": "turn.failed", "error": {"message": failed}})
    else:
        events.extend([
            {"type": "item.completed", "item": {
                "id": "item_1", "type": "agent_message", "text": result}},
            {"type": "turn.completed", "usage": {
                "input_tokens": in_tok, "cached_input_tokens": 0,
                "output_tokens": out_tok}},
        ])
    return "\n".join(json.dumps(event) for event in events) + "\n"


def _patch(monkeypatch, *, stdout=None, returncode=0, stderr="", capture=None):
    if stdout is None:
        stdout = _codex_jsonl()

    def fake_run(cmd, **kwargs):
        if capture is not None:
            capture["cmd"] = cmd
            capture["kwargs"] = kwargs
        return _completed(stdout=stdout, returncode=returncode, stderr=stderr)

    monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)


def _run(runner, *, tools=(), model="gpt-5.6-terra"):
    return runner.run(name="writer", system="SYS", user="USER", model=model,
                      tools=list(tools), max_tokens=8000, logger=None)


def test_returns_last_agent_message(monkeypatch):
    _patch(monkeypatch, stdout=_codex_jsonl(result="Hello world"))
    assert _run(CLIAgentRunner()) == "Hello world"


def test_records_subscription_usage(monkeypatch):
    _patch(monkeypatch, stdout=_codex_jsonl(in_tok=100, out_tok=200))
    runner = CLIAgentRunner()
    _run(runner, model="gpt-5.6-sol")
    assert runner.records == [{
        "agent": "writer", "model": "gpt-5.6-sol",
        "input_tokens": 100, "output_tokens": 200, "usd": 0.0,
    }]


def test_argv_no_tools_is_ephemeral_and_has_no_local_shell(monkeypatch):
    cap = {}
    _patch(monkeypatch, capture=cap)
    _run(CLIAgentRunner(codex_bin="/opt/codex"), tools=[])
    cmd = cap["cmd"]
    assert cmd[:4] == ["/opt/codex", "exec", "--model", "gpt-5.6-terra"]
    assert "--ephemeral" in cmd and "--ignore-user-config" in cmd
    assert "--ignore-rules" in cmd and "--json" in cmd
    assert "shell_tool" in cmd and "unified_exec" in cmd
    assert 'web_search="disabled"' in cmd
    assert cap["kwargs"]["input"] == "SYS\n\n---\n\nUSER"


def test_argv_websearch_gated(monkeypatch):
    cap = {}
    _patch(monkeypatch, capture=cap)
    _run(CLIAgentRunner(), tools=["WebSearch"])
    assert 'web_search="live"' in cap["cmd"]


def test_unsupported_tool_fails_before_spawn():
    with pytest.raises(ClientError, match="unsupported Codex tools"):
        _run(CLIAgentRunner(), tools=["Bash"])


def test_api_credentials_stripped_from_env(monkeypatch):
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
        monkeypatch.setenv(key, "must-be-dropped")
    cap = {}
    _patch(monkeypatch, capture=cap)
    _run(CLIAgentRunner())
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"):
        assert key not in cap["kwargs"]["env"]


def test_nonzero_exit_raises(monkeypatch):
    _patch(monkeypatch, stdout="", returncode=1, stderr="boom")
    with pytest.raises(ClientError):
        _run(CLIAgentRunner())


def test_non_json_stdout_raises(monkeypatch):
    _patch(monkeypatch, stdout="not json at all")
    with pytest.raises(ClientError):
        _run(CLIAgentRunner())


def test_turn_failure_raises(monkeypatch):
    _patch(monkeypatch, stdout=_codex_jsonl(failed="model unavailable"))
    with pytest.raises(ClientError, match="model unavailable"):
        _run(CLIAgentRunner())


def test_empty_result_raises(monkeypatch):
    _patch(monkeypatch, stdout=_codex_jsonl(result="   "))
    with pytest.raises(RuntimeError):
        _run(CLIAgentRunner())


def test_timeout_raises_clienterror(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 600)

    monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
    with pytest.raises(ClientError):
        _run(CLIAgentRunner())

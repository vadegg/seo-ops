"""Agent output validation + one-shot re-prompt (#6)."""

import json

import pytest

from agents import editor
from agents.runner import run_json
from agents.validation import (EDITOR_CHECKS, ValidationError, validate_researcher,
                               validate_strategist, validate_outliner,
                               validate_editor)
from clients.retry import ClientError


class ScriptedRunner:
    """Returns a fixed sequence of replies, one per call."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0
        self.prompts = []

    def run(self, *, name, system, user, model, tools, max_tokens, logger):
        self.calls += 1
        self.prompts.append(user)
        return self._replies.pop(0)


VALID_RESEARCHER = json.dumps({
    "candidates": [{"keyword": "k", "score": 0.5}]})


def _run(runner):
    return run_json(runner, name="researcher", system="s", user="u",
                    model="m", tools=[], max_tokens=100, logger=None,
                    validate=validate_researcher)


def test_valid_first_try_no_retry():
    r = ScriptedRunner([VALID_RESEARCHER])
    out = _run(r)
    assert out["candidates"][0]["keyword"] == "k"
    assert r.calls == 1


def test_broken_then_valid_retries_once():
    r = ScriptedRunner(["not json at all", VALID_RESEARCHER])
    out = _run(r)
    assert out["candidates"][0]["keyword"] == "k"
    assert r.calls == 2


def test_schema_violation_then_valid_retries_once():
    # parseable JSON but wrong shape (no candidates) -> one retry
    r = ScriptedRunner([json.dumps({"candidates": []}), VALID_RESEARCHER])
    out = _run(r)
    assert out["candidates"]
    assert r.calls == 2


def test_invalid_twice_raises_client_error():
    r = ScriptedRunner(["garbage", json.dumps({"nope": 1})])
    with pytest.raises(ClientError):
        _run(r)
    assert r.calls == 2


def test_each_validator_rejects_bad_shapes():
    with pytest.raises(ValidationError):
        validate_researcher({"candidates": [{"keyword": "", "score": 1}]})
    with pytest.raises(ValidationError):
        validate_strategist({"topic": "t", "primary_keyword": "p",
                             "score": "high"})
    with pytest.raises(ValidationError):
        validate_outliner({"title": "t", "sections": []})
    with pytest.raises(ValidationError):
        validate_editor({"edited_markdown": "x", "critique": "no"})


def _editor_reply(body):
    return {"edited_markdown": body, "critique": {
        "checklist": {key: True for key in EDITOR_CHECKS},
        "passed": True, "notes": "approved"}}


@pytest.mark.parametrize("body,reason", [
    ('---\ntitle: "Top task analysis"\nmeta_description: "Example"\n'
     'slug: "top-task-analysis"\n---\n\n## Tasks\n\nText.', "frontmatter"),
    ('\n  ---\ntitle: "Example"\n---\n\nText.', "frontmatter"),
    ("# Article title\n\n## Tasks\n\nText.", "H1"),
    ('## Tasks\n\n<script type="application/ld+json">{}</script>', "script"),
    ('## Tasks\n\n<SCRIPT>alert(1)</SCRIPT>', "script"),
])
def test_editor_repairs_invalid_body_before_approval(body, reason):
    invalid = _editor_reply(body)
    with pytest.raises(ValidationError, match=reason):
        validate_editor(invalid)
    corrected = _editor_reply("## Tasks\n\nText.\n\n---\n\n### Next steps\n")
    runner = ScriptedRunner([json.dumps(invalid), json.dumps(corrected)])
    result = editor.run(runner, model="fake", tools=[], max_tokens=100,
                        logger=None, draft_md="## Tasks\n\nText.", brief={},
                        style_guide="", evidence_passages=[], iteration=1)
    assert result == corrected
    assert runner.calls == 2
    assert reason in runner.prompts[1]

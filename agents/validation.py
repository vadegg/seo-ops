"""Lightweight, dependency-free output validation for agents (#6).

Best-effort ``extract_json`` tolerates malformed replies but lets a
structurally-broken object (no ``title``, no ``sections``, garbage
``score``) ride downstream into a weak post or a ``KeyError`` in the
assembler. These validators assert the *shape and ranges* each agent's
contract promises; ``runner.run_json`` calls them and re-prompts once on
failure before raising ``ClientError`` (so the ladder/alerts engage).

A tiny internal checker is used instead of ``jsonschema``/``pydantic`` to
keep the test suite free of third-party dependencies and network.
"""

from __future__ import annotations

import math
import re

EDITOR_CHECKS = ("on_brief", "style_guide", "seo", "internal_links",
                 "evidence_grounded", "first_hand_present")


class ValidationError(ValueError):
    """An agent's output does not match its declared contract."""


def _require_dict(data, agent: str) -> dict:
    if not isinstance(data, dict):
        raise ValidationError(f"{agent}: expected a JSON object, got "
                              f"{type(data).__name__}")
    return data


def _nonempty_str(data: dict, key: str, agent: str) -> None:
    v = data.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValidationError(f"{agent}: '{key}' must be a non-empty string")


def _is_number(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def validate_researcher(data) -> None:
    d = _require_dict(data, "researcher")
    cands = d.get("candidates")
    if not isinstance(cands, list) or not cands:
        raise ValidationError("researcher: 'candidates' must be a non-empty list")
    for i, c in enumerate(cands):
        if not isinstance(c, dict):
            raise ValidationError(f"researcher: candidate[{i}] is not an object")
        if not isinstance(c.get("keyword"), str) or not c["keyword"].strip():
            raise ValidationError(
                f"researcher: candidate[{i}].keyword must be a non-empty string")
        if not _is_number(c.get("score")) or not 0 <= c["score"] <= 1:
            raise ValidationError(
                f"researcher: candidate[{i}].score must be a number")


def validate_strategist(data) -> None:
    d = _require_dict(data, "strategist")
    _nonempty_str(d, "topic", "strategist")
    _nonempty_str(d, "primary_keyword", "strategist")
    if not _is_number(d.get("score")) or not 0 <= d["score"] <= 1:
        raise ValidationError("strategist: 'score' must be a number")


def validate_outliner(data) -> None:
    d = _require_dict(data, "outliner")
    _nonempty_str(d, "title", "outliner")
    if not isinstance(d.get("sections"), list) or not d["sections"]:
        raise ValidationError("outliner: 'sections' must be a non-empty list")


def validate_article_body(body) -> None:
    """Body-only contract shared by editorial retries and publication checks."""
    if not isinstance(body, str) or not body.strip():
        raise ValidationError("article body must be a non-empty string")
    if body.lstrip().startswith("---"):
        raise ValidationError("article body contains YAML frontmatter; remove "
                              "the metadata block and return only the body")
    if re.search(r"^#\s", body, re.M):
        raise ValidationError("article body contains H1; remove the title "
                              "heading and start body headings at H2")
    if re.search(r"<script\b", body, re.I):
        raise ValidationError("article body contains script tags; remove them")


def validate_editor(data) -> None:
    d = _require_dict(data, "editor")
    if not isinstance(d.get("edited_markdown"), str) or not d["edited_markdown"].strip():
        raise ValidationError("editor: 'edited_markdown' must be a non-empty string")
    validate_article_body(d["edited_markdown"])
    if not isinstance(d.get("critique"), dict):
        raise ValidationError("editor: 'critique' must be an object")
    critique = d["critique"]
    checklist = critique.get("checklist")
    if not isinstance(checklist, dict) or any(
            type(checklist.get(key)) is not bool for key in EDITOR_CHECKS):
        raise ValidationError("editor: checklist must include every required "
                              "boolean: " + ", ".join(EDITOR_CHECKS))
    if type(critique.get("passed")) is not bool:
        raise ValidationError("editor: 'passed' must be a boolean")
    if critique["passed"] and not all(checklist[key] for key in EDITOR_CHECKS):
        raise ValidationError("editor: passed=true contradicts failed checks")

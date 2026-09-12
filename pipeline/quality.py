"""Required publication checks shared by full and selected-step runs."""

from __future__ import annotations

import re

from agents.validation import validate_editor
from pipeline import artifacts as A, dedupe
from pipeline.escalation import SCORE_THRESHOLD


class QualityError(RuntimeError):
    """Keep artifacts for review instead of publishing a rejected article."""


def topic_rejection(topic: dict, history: dict) -> str:
    from agents.validation import validate_strategist
    validate_strategist(topic)
    duplicate = dedupe.duplicate_of_published(topic, history)
    if duplicate:
        return f"topic duplicates published post '{duplicate.get('slug', '?')}'"
    if topic['score'] < SCORE_THRESHOLD:
        return f"topic score {topic['score']:.2f} below threshold {SCORE_THRESHOLD:.2f}"
    return ""


def require_review(body: str, critique: dict) -> None:
    validate_editor({"edited_markdown": body, "critique": critique})
    if not critique["passed"]:
        raise QualityError("editor rejected the article; draft retained for review")


def require_final_review(ctx) -> None:
    from agents.humanizer import preservation_errors
    approved = ctx.store.read_text(A.EDITOR_MD)
    body = ctx.store.read_text(A.HUMANIZER)
    if body == approved:
        return
    if preservation_errors(approved, body):
        raise QualityError("final body changed protected article properties")
    artifact = "05c-humanizer.critique.json"
    if not ctx.store.exists(artifact):
        raise QualityError("changed humanizer output requires final editorial approval")
    require_review(body, ctx.store.read_json(artifact))


def require_publishable(ctx) -> None:
    topic = ctx.store.read_json(A.STRATEGIST)
    reason = topic_rejection(topic, ctx.stores["topic_history"])
    if reason:
        raise QualityError(reason)
    require_review(ctx.store.read_text(A.EDITOR_MD),
                   ctx.store.read_json(A.EDITOR_CRITIQUE))
    require_final_review(ctx)
    body = ctx.store.read_text(A.HUMANIZER)
    if (not body.strip() or body.lstrip().startswith("---")
            or re.search(r"^#\s|<script\b", body, re.M | re.I)):
        raise QualityError("article body is empty or contains H1/frontmatter/script")
    if not ctx.store.exists(A.UNIQUENESS):
        raise QualityError("uniqueness check is required before assembly/publication")
    if ctx.store.read_json(A.UNIQUENESS).get("above_threshold"):
        raise QualityError("article repeats a published body")
    if (ctx.stores["topic_history"].get("published")
            and not ctx.store.read_json(A.UNIQUENESS).get("corpus_size")):
        raise QualityError("published history exists but uniqueness corpus is empty; refresh the blog clone")

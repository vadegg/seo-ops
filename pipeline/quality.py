"""Required publication checks shared by full and selected-step runs."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from agents.validation import ValidationError, validate_article_body, validate_editor, validate_outliner
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


def require_source_citations(brief: dict, body: str) -> None:
    """Check traceability; factual support is still an editorial judgement.

    Validate resumed briefs too. Retrieval belongs to the Outliner; the
    Editor compares claims with its source excerpts before approving.
    """
    try:
        validate_outliner(brief)
    except ValidationError as exc:
        raise QualityError(f"brief requires revision: {exc}") from exc
    links = set(re.findall(r"(?<!!)\[[^\]\n]+\]\((https://[^\s)]+)\)", body))
    sources = {source["url"] for source in brief["sources"]}
    for link in sources - links:
        raise QualityError(f"verified source is not cited in final body: {link}")
    for link in links - sources:
        if urlsplit(link).hostname not in {"glasgow.works", "blog.glasgow.works"}:
            raise QualityError(f"external citation is missing from verified brief: {link}")


def require_publishable(ctx) -> None:
    topic = ctx.store.read_json(A.STRATEGIST)
    reason = topic_rejection(topic, ctx.stores["topic_history"])
    if reason:
        raise QualityError(reason)
    require_review(ctx.store.read_text(A.EDITOR_MD),
                   ctx.store.read_json(A.EDITOR_CRITIQUE))
    require_final_review(ctx)
    body = ctx.store.read_text(A.HUMANIZER)
    require_source_citations(ctx.store.read_json(A.OUTLINER), body)
    try:
        validate_article_body(body)
    except ValidationError as exc:
        raise QualityError(str(exc)) from exc
    if not ctx.store.exists(A.UNIQUENESS):
        raise QualityError("uniqueness check is required before assembly/publication")
    if ctx.store.read_json(A.UNIQUENESS).get("above_threshold"):
        raise QualityError("article repeats a published body")
    if (ctx.stores["topic_history"].get("published")
            and not ctx.store.read_json(A.UNIQUENESS).get("corpus_size")):
        raise QualityError("published history exists but uniqueness corpus is empty; refresh the blog clone")

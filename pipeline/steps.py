"""Per-step pipeline units + a generic driver.

Each of the 7 agents/code steps is a self-contained function that reads
its inputs from disk (artifacts + persistent stores on the context) and
writes exactly one output artifact. Because every step is independent,
they can be run in isolation, resumed, or chained — the orchestrator's
full run reuses the very same functions.

Escalation is NOT handled here: an isolated step does a single pass at
``ctx.stage`` (from ``--start-stage``). The auto-escalation loop lives in
``pipeline.orchestrator`` and only runs for the no-flag full pipeline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from clients.retry import ClientError
from clients.websearch import tools_for_stage
from logging_setup import escalation_log, get_agent_logger
from pipeline import artifacts as A
from pipeline.artifacts import ArtifactStore
from pipeline.assembler import assemble
from pipeline.escalation import STAGES
from pipeline.publisher import publish

from agents import editor, outliner, researcher, strategist, writer


# --------------------------------------------------------------------------
# Context shared by every step
# --------------------------------------------------------------------------
@dataclass
class StepContext:
    cfg: object
    deps: object
    store: ArtifactStore
    stores: dict
    run_dir: Path
    run_date: str
    dry_run: bool = False
    stage: int = 1            # escalation stage to use for a single pass
    force: bool = False
    logger: object = None


def model_for_stage(cfg, stage: int) -> str:
    key = STAGES[stage].model_key
    return cfg.model_opus if key == "opus" else cfg.model_sonnet


# --------------------------------------------------------------------------
# Shared helpers (also used by the full-run escalation loop)
# --------------------------------------------------------------------------
def gather_research_context(cfg, deps, spec, logger):
    """Deterministic GSC/DataForSEO gathering with retry. On persistent
    API failure return empty + a flag so the ladder can move to a
    non-dependent stage instead of crashing."""
    gsc_rows: list = []
    dfs_metrics: list = []
    api_failed = False

    if spec.use_gsc:
        try:
            min_pos, max_pos = (5.0, 20.0) if spec.stage == 1 else (3.0, 40.0)
            gsc_rows = deps.gsc.near_top_queries(min_pos=min_pos,
                                                 max_pos=max_pos)
        except ClientError as e:
            if logger:
                logger.warning("GSC unavailable at stage %d: %s", spec.stage, e)
            api_failed = True

    if spec.use_dataforseo and gsc_rows:
        try:
            seeds = [r["query"] for r in gsc_rows[:60]]
            dfs_metrics = deps.dataforseo.keyword_metrics(seeds)
        except ClientError as e:
            if logger:
                logger.warning("DataForSEO unavailable at stage %d: %s",
                               spec.stage, e)
            api_failed = True

    return gsc_rows, dfs_metrics, api_failed


def researcher_pass(ctx: StepContext, spec, model, tools,
                    gsc_rows, dfs_metrics) -> dict:
    """One Researcher invocation -> writes 01. Used by both the isolated
    step and the full-run escalation loop."""
    candidates = researcher.run(
        ctx.deps.agent_runner, model=model, tools=tools,
        max_tokens=ctx.cfg.agent_max_tokens, logger=ctx.logger,
        stage_spec=spec, backlog=ctx.stores["backlog"],
        topic_history=ctx.stores["topic_history"],
        gsc_rows=gsc_rows, dfs_metrics=dfs_metrics,
        seed_topics=ctx.stores["seed_topics"])
    ctx.store.write_json(A.RESEARCHER, candidates)
    return candidates


def strategist_pass(ctx: StepContext, model, stage: int) -> dict:
    """One Strategist invocation -> reads 01, writes 02 (stamped with the
    escalation stage so downstream steps pick the right model)."""
    candidates = ctx.store.read_json(A.RESEARCHER)
    topic = strategist.run(
        ctx.deps.agent_runner, model=model, tools=[],
        max_tokens=ctx.cfg.agent_max_tokens, logger=ctx.logger,
        candidates=candidates, topic_history=ctx.stores["topic_history"],
        content_map=ctx.stores["content_map"])
    topic["_escalation_stage"] = stage
    ctx.store.write_json(A.STRATEGIST, topic)
    return topic


def ensure_evidence(ctx: StepContext, brief: dict | None = None) -> list:
    """Deterministic BM25 evidence retrieval, cached as 03b (resume)."""
    if ctx.store.exists(A.EVIDENCE):
        return ctx.store.read_json(A.EVIDENCE)
    brief = brief if brief is not None else ctx.store.read_json(A.OUTLINER)
    topic = (ctx.store.read_json(A.STRATEGIST)
             if ctx.store.exists(A.STRATEGIST) else {})
    q = " ".join([brief.get("primary_keyword", "")]
                 + list(brief.get("secondary_keywords", []))
                 + [topic.get("topic", "")])
    try:
        evidence = ctx.deps.evidence.search(q, k=8)
    except Exception as e:  # noqa: BLE001
        if ctx.logger:
            ctx.logger.warning("evidence retrieval failed: %s", e)
        evidence = []
    ctx.store.write_json(A.EVIDENCE, evidence)

    # #4: a post with no first-hand fact is a real degradation — make it
    # visible. Distinguish an empty/missing corpus from a query that simply
    # matched nothing, so the fix is obvious.
    if ctx.logger:
        n = len(evidence)
        if n > 0:
            ctx.logger.info("evidence: %d passage(s) retrieved", n)
        elif not _corpus_has_documents(ctx):
            ctx.logger.warning("evidence empty: corpus directory has no "
                               "indexable documents (EVIDENCE_DIR)")
        else:
            ctx.logger.warning("evidence empty: corpus is populated but the "
                               "query matched no passages")
    return evidence


def _corpus_has_documents(ctx: StepContext) -> bool:
    """True if EVIDENCE_DIR holds at least one .md/.txt file."""
    d = getattr(ctx.cfg, "evidence_dir", None)
    if not d:
        return False
    try:
        return any(p.suffix.lower() in {".md", ".txt"} and p.is_file()
                   for p in Path(d).rglob("*"))
    except OSError:
        return False


# --------------------------------------------------------------------------
# The 7 steps (each reads inputs from ctx.store, writes one output)
# --------------------------------------------------------------------------
def step_researcher(ctx: StepContext) -> None:
    spec = STAGES[ctx.stage]
    model = model_for_stage(ctx.cfg, ctx.stage)
    tools = tools_for_stage(spec.stage) if spec.use_websearch else []
    gsc_rows, dfs_metrics, _ = gather_research_context(
        ctx.cfg, ctx.deps, spec, ctx.logger)
    researcher_pass(ctx, spec, model, tools, gsc_rows, dfs_metrics)


def step_strategist(ctx: StepContext) -> None:
    strategist_pass(ctx, model_for_stage(ctx.cfg, ctx.stage), ctx.stage)


def select_relevant_links(internal_links: dict, topic: dict,
                          k: int = 8) -> dict:
    """Pass the Outliner a cluster-relevant *subset* of the link map rather
    than the whole list (#13), so it links contextually instead of at random.
    Hubs are always included; posts are ranked by keyword overlap with the
    topic's cluster + keywords, falling back to most-recent."""
    posts = list(internal_links.get("posts", []))
    terms = set()
    for s in [topic.get("cluster", ""), topic.get("topic", ""),
              topic.get("primary_keyword", "")] + list(
                  topic.get("secondary_keywords", [])):
        terms |= {w for w in re.split(r"[^a-z0-9]+", str(s).lower()) if len(w) > 3}

    def overlap(p: dict) -> int:
        hay = f"{p.get('cluster','')} {p.get('title','')}".lower()
        words = {w for w in re.split(r"[^a-z0-9]+", hay) if len(w) > 3}
        return len(words & terms)

    ranked = sorted(posts, key=lambda p: (overlap(p), p.get("date", "")),
                    reverse=True)
    chosen = [p for p in ranked if overlap(p) > 0][:k]
    if not chosen:  # no semantic hit — still offer the most recent few
        chosen = ranked[:min(k, 3)]
    return {"hubs": internal_links.get("hubs", {}), "posts": chosen}


def step_outliner(ctx: StepContext) -> None:
    topic = ctx.store.read_json(A.STRATEGIST)
    stage = int(topic.get("_escalation_stage", ctx.stage))
    full_map = ctx.stores["internal_links"]
    corpus = len(full_map.get("posts", []))
    relevant = select_relevant_links(full_map, topic)
    min_links = (ctx.cfg.internal_link_floor
                 if corpus >= ctx.cfg.internal_link_min_corpus else 0)
    brief = outliner.run(
        ctx.deps.agent_runner, model=model_for_stage(ctx.cfg, stage),
        tools=tools_for_stage(stage), max_tokens=ctx.cfg.agent_max_tokens,
        logger=ctx.logger, topic=topic,
        content_map=ctx.stores["content_map"],
        internal_links=relevant, min_links=min_links)
    ctx.store.write_json(A.OUTLINER, brief)


def step_writer(ctx: StepContext) -> None:
    brief = ctx.store.read_json(A.OUTLINER)
    evidence = ensure_evidence(ctx, brief)
    draft = writer.run(
        ctx.deps.agent_runner, model=ctx.cfg.model_sonnet, tools=[],
        max_tokens=ctx.cfg.agent_max_tokens, logger=ctx.logger,
        brief=brief, style_guide=ctx.stores["style_guide"],
        evidence_passages=evidence)
    ctx.store.write_text(A.WRITER, draft)


def step_editor(ctx: StepContext) -> None:
    """Self-critique: 2 iterations, then a forced Opus final rewrite +
    hard alert. Never abandons the day."""
    draft = ctx.store.read_text(A.WRITER)
    brief = ctx.store.read_json(A.OUTLINER)
    evidence = ensure_evidence(ctx, brief)

    current = draft
    for iteration in (1, 2):
        try:
            result = editor.run(
                ctx.deps.agent_runner, model=ctx.cfg.model_sonnet, tools=[],
                max_tokens=ctx.cfg.agent_max_tokens, logger=ctx.logger,
                draft_md=current, brief=brief,
                style_guide=ctx.stores["style_guide"],
                evidence_passages=evidence, iteration=iteration)
        except ClientError as e:
            # Persistently-invalid editor output must NOT abandon the day —
            # fall through to the forced final on the best draft so far.
            if ctx.logger:
                ctx.logger.warning("editor iteration %d failed validation "
                                   "(%s) — continuing", iteration, e)
            continue
        current = result["edited_markdown"]
        critique = result["critique"]
        if critique.get("passed"):
            ctx.store.write_text(A.EDITOR_MD, current)
            ctx.store.write_json(A.EDITOR_CRITIQUE, critique)
            return

    if ctx.logger:
        ctx.logger.warning("editor did not pass in 2 iterations — "
                           "final Opus rewrite")
    try:
        result = editor.run(
            ctx.deps.agent_runner, model=ctx.cfg.model_opus, tools=[],
            max_tokens=ctx.cfg.agent_max_tokens, logger=ctx.logger,
            draft_md=current, brief=brief,
            style_guide=ctx.stores["style_guide"],
            evidence_passages=evidence, iteration=3, final=True)
        current = result["edited_markdown"]
        critique = result["critique"]
    except ClientError as e:
        # Last-resort fallback: ship the best draft we have rather than crash
        # the run. The "never abandons the day" contract wins over a clean
        # critique — the hard alert below still flags it for manual review.
        if ctx.logger:
            ctx.logger.warning("editor forced-final also failed validation "
                               "(%s) — shipping best available draft", e)
        critique = {"checklist": {}, "passed": False,
                    "notes": f"forced-final validation failed: {e}"}
    critique["forced_final"] = True
    escalation_log(ctx.run_dir, "editor: forced Opus final rewrite after 2 fails")
    # ERROR so the digest escalates to a hard alert and the run-log summary
    # flags it; the consolidated digest (#8) carries the user-facing note.
    if ctx.logger:
        ctx.logger.error("editor did not pass the checklist in 2 iterations — "
                         "published the best (Opus) version; needs manual review")
    ctx.store.write_text(A.EDITOR_MD, current)
    ctx.store.write_json(A.EDITOR_CRITIQUE, critique)


def step_assembler(ctx: StepContext) -> None:
    """Deterministic build. Persists a meta sidecar (slug) so the
    Publisher needs nothing else."""
    edited = ctx.store.read_text(A.EDITOR_MD)
    brief = ctx.store.read_json(A.OUTLINER)
    topic = ctx.store.read_json(A.STRATEGIST)
    assembled = assemble(
        edited_markdown=edited, brief=brief, topic=topic,
        internal_links=ctx.stores["internal_links"],
        site_name=ctx.cfg.site_name, base_url=ctx.cfg.blog_base_url,
        run_date=ctx.run_date,
        author_name=ctx.cfg.author_name, author_slug=ctx.cfg.author_slug,
        default_category=ctx.cfg.default_category,
        cta_text=ctx.cfg.cta_text, cta_url=ctx.cfg.cta_url,
        tool_disclosure=ctx.cfg.tool_disclosure,
        author_url=ctx.cfg.author_url,
        author_same_as=ctx.cfg.author_same_as,
        org_same_as=ctx.cfg.org_same_as,
        default_og_image=ctx.cfg.default_og_image,
        internal_link_floor=ctx.cfg.internal_link_floor,
        internal_link_min_corpus=ctx.cfg.internal_link_min_corpus,
        logger=ctx.logger)
    ctx.store.write_text(A.ASSEMBLER, assembled.markdown)
    ctx.store.write_json(A.ASSEMBLER_META, {"slug": assembled.slug})


def step_publisher(ctx: StepContext) -> None:
    post_md = ctx.store.read_text(A.ASSEMBLER)
    brief = ctx.store.read_json(A.OUTLINER)
    topic = ctx.store.read_json(A.STRATEGIST)
    stage = int(topic.get("_escalation_stage", ctx.stage))

    if ctx.store.exists(A.ASSEMBLER_META):
        meta = ctx.store.read_json(A.ASSEMBLER_META)
    else:  # fallback for legacy runs without the sidecar
        from pipeline.assembler import _slugify
        meta = {"slug": _slugify(brief.get("slug") or brief.get("title")
                                 or topic.get("topic", "post"))}

    assembled = SimpleNamespace(markdown=post_md, slug=meta["slug"])

    research = (ctx.store.read_json(A.RESEARCHER)
                if ctx.store.exists(A.RESEARCHER) else {})

    status = publish(
        cfg=ctx.cfg, assembled=assembled, brief=brief, topic=topic,
        stage=stage, run_date=ctx.run_date, dry_run=ctx.dry_run,
        git_client=ctx.deps.git, logger=ctx.logger,
        candidates=research.get("candidates") or [],
        surplus=research.get("backlog_surplus") or [],
        published_keyword=brief.get("primary_keyword", ""))
    ctx.store.write_json(A.PUBLISHER, status)
    # The per-publish Telegram message is gone: the orchestrator sends one
    # consolidated end-of-run digest instead (#8), reusing _publish_report.


def _publish_report(ctx, brief: dict, topic: dict, status: dict,
                    post_md: str, stage: int) -> str:
    """Human-readable Russian publish summary: topic, link, description,
    and what is queued next in the backlog."""
    m = re.search(r'^description:\s*"(.*)"\s*$', post_md, re.MULTILINE)
    desc = m.group(1) if m else brief.get("meta_description", "")
    title = brief.get("title") or topic.get("topic") or status.get("slug", "")

    nxt: list[str] = []
    try:
        data = json.loads((ctx.cfg.backlog_dir / "keyword_backlog.json")
                          .read_text(encoding="utf-8"))
        cands = sorted(data.get("candidates", []),
                       key=lambda c: c.get("score", 0), reverse=True)
        nxt = [f"  • {c['keyword']} ({float(c.get('score', 0)):.2f})"
               for c in cands[:3] if c.get("keyword")]
    except (OSError, ValueError):
        pass

    header = ("Опубликована новая статья" if status.get("status") == "published"
              else "Статья собрана")
    lines = [
        header,
        "",
        f"📝 Тема: {title}",
        f"🔗 {status.get('url', '(нет URL)')}",
        f"📄 {desc}",
        "",
        f"🎯 Стадия эскалации: {stage}",
    ]
    bk = status.get("backlog") or {}
    if bk:
        lines.append(
            f"📦 В бэклог добавлено: +{bk.get('added', 0)} "
            f"(всего в резерве: {bk.get('kept', 0)})")
    if nxt:
        lines.append("⏭️ Следующие кандидаты на очереди:")
        lines.extend(nxt)
    return "\n".join(lines)


def build_digest(ctx: StepContext, accumulator, usage_report: dict) -> tuple:
    """One consolidated end-of-run message (#8): publish summary +
    degradations (#3) + token/$ usage (#5). Returns (text, level) where
    level is hard on a forced-final, warn on any degradation, else info.
    """
    store = ctx.store
    status = store.read_json(A.PUBLISHER) if store.exists(A.PUBLISHER) else {}
    brief = store.read_json(A.OUTLINER) if store.exists(A.OUTLINER) else {}
    topic = store.read_json(A.STRATEGIST) if store.exists(A.STRATEGIST) else {}
    post_md = store.read_text(A.ASSEMBLER) if store.exists(A.ASSEMBLER) else ""
    stage = int(status.get("escalation_stage", ctx.stage))

    critique = (store.read_json(A.EDITOR_CRITIQUE)
                if store.exists(A.EDITOR_CRITIQUE) else {})
    forced = bool(critique.get("forced_final"))
    degr = list(getattr(accumulator, "degradations", []))
    has_error = any(d["level"] == "ERROR" for d in degr)

    level = "hard" if (forced or has_error) else (
        "warn" if degr else "info")

    # An isolated step subset may not have produced a publishable post; only
    # render the publish summary when there's something to report.
    if status or brief or topic:
        parts = [_publish_report(ctx, brief, topic, status, post_md, stage)]
        if status.get("status") == "dry_run":
            parts[0] = "[dry-run] " + parts[0]
    else:
        parts = [f"Прогон завершён ({ctx.run_date})"]

    if degr:
        parts.append("")
        parts.append(f"⚠️ Деградации ({len(degr)}):")
        parts.extend(f"  • {d['level']} | {d['agent']} | {d['message']}"
                     for d in degr)

    # One run == one article, so the run total IS the cost of writing this
    # article (#5). State it explicitly so it reads as the article's price.
    total = usage_report.get("total", {}) if usage_report else {}
    if total:
        def _n(v: int) -> str:           # space thousands separator
            return f"{int(v):,}".replace(",", " ")
        parts.append("")
        parts.append(
            f"💸 Стоимость статьи: ~${total.get('usd', 0):.2f} "
            f"({_n(total.get('input_tokens', 0))} вход / "
            f"{_n(total.get('output_tokens', 0))} выход токенов)")

    return "\n".join(parts), level


# --------------------------------------------------------------------------
# Step registry + generic driver
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Step:
    name: str
    inputs: tuple[str, ...]   # required input artifact constants
    output: str               # output artifact constant
    fn: Callable[[StepContext], None]


STEPS: list[Step] = [
    Step("researcher", (), A.RESEARCHER, step_researcher),
    Step("strategist", (A.RESEARCHER,), A.STRATEGIST, step_strategist),
    Step("outliner", (A.STRATEGIST,), A.OUTLINER, step_outliner),
    Step("writer", (A.OUTLINER,), A.WRITER, step_writer),
    Step("editor", (A.WRITER, A.OUTLINER), A.EDITOR_MD, step_editor),
    Step("assembler", (A.EDITOR_MD, A.OUTLINER, A.STRATEGIST), A.ASSEMBLER,
         step_assembler),
    Step("publisher", (A.ASSEMBLER, A.OUTLINER, A.STRATEGIST), A.PUBLISHER,
         step_publisher),
]

STEP_NAMES: list[str] = [s.name for s in STEPS]
_BY_NAME: dict[str, Step] = {s.name: s for s in STEPS}


class StepInputError(RuntimeError):
    """A selected step's required input artifact is missing on disk."""


def _prev_step(step: Step) -> str | None:
    i = STEP_NAMES.index(step.name)
    return STEP_NAMES[i - 1] if i > 0 else None


def run_steps(ctx: StepContext, selected: list[str]) -> None:
    """Run the given steps in pipeline order, honouring resume and input
    validation. Output already on disk -> skip (unless ctx.force).
    Missing required input -> StepInputError with a fix hint."""
    chosen = set(selected)
    for step in STEPS:
        if step.name not in chosen:
            continue

        if ctx.store.exists(step.output) and not ctx.force:
            if ctx.logger:
                ctx.logger.info("skip %s: %s already present (resume)",
                                step.name, step.output)
            continue

        for inp in step.inputs:
            if not ctx.store.exists(inp):
                hint = _prev_step(step)
                raise StepInputError(
                    f"cannot run '{step.name}': missing input {inp}"
                    + (f" — run '{hint}' first" if hint else ""))

        # Tag every line this step emits (incl. SDKAgentRunner's "agent ->
        # model" line) with the real agent name instead of "orchestrator".
        orig_logger = ctx.logger
        if orig_logger is not None:
            ctx.logger = get_agent_logger(step.name)
        try:
            if ctx.logger:
                ctx.logger.info("=== step %s (stage %d) ===",
                                step.name, ctx.stage)
            step.fn(ctx)
        finally:
            ctx.logger = orig_logger

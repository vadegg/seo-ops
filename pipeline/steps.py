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
from dataclasses import dataclass, field
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
    return evidence


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


def step_outliner(ctx: StepContext) -> None:
    topic = ctx.store.read_json(A.STRATEGIST)
    stage = int(topic.get("_escalation_stage", ctx.stage))
    brief = outliner.run(
        ctx.deps.agent_runner, model=model_for_stage(ctx.cfg, stage),
        tools=tools_for_stage(stage), max_tokens=ctx.cfg.agent_max_tokens,
        logger=ctx.logger, topic=topic,
        content_map=ctx.stores["content_map"],
        internal_links=ctx.stores["internal_links"])
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
        result = editor.run(
            ctx.deps.agent_runner, model=ctx.cfg.model_sonnet, tools=[],
            max_tokens=ctx.cfg.agent_max_tokens, logger=ctx.logger,
            draft_md=current, brief=brief,
            style_guide=ctx.stores["style_guide"],
            evidence_passages=evidence, iteration=iteration)
        current = result["edited_markdown"]
        critique = result["critique"]
        if critique.get("passed"):
            ctx.store.write_text(A.EDITOR_MD, current)
            ctx.store.write_json(A.EDITOR_CRITIQUE, critique)
            return

    if ctx.logger:
        ctx.logger.warning("editor did not pass in 2 iterations — "
                           "final Opus rewrite")
    result = editor.run(
        ctx.deps.agent_runner, model=ctx.cfg.model_opus, tools=[],
        max_tokens=ctx.cfg.agent_max_tokens, logger=ctx.logger,
        draft_md=current, brief=brief, style_guide=ctx.stores["style_guide"],
        evidence_passages=evidence, iteration=3, final=True)
    current = result["edited_markdown"]
    critique = result["critique"]
    critique["forced_final"] = True
    escalation_log(ctx.run_dir, "editor: forced Opus final rewrite after 2 fails")
    if ctx.deps.telegram:
        ctx.deps.telegram.send(
            "Редактор не прошёл чеклист за 2 попытки — опубликована лучшая "
            "версия (Opus). Нужна ручная проверка.", level="hard")
    ctx.store.write_text(A.EDITOR_MD, current)
    ctx.store.write_json(A.EDITOR_CRITIQUE, critique)


def step_assembler(ctx: StepContext) -> None:
    """Deterministic build + confidentiality scrub. Persists a meta
    sidecar (slug/leak) so the Publisher needs nothing else."""
    edited = ctx.store.read_text(A.EDITOR_MD)
    brief = ctx.store.read_json(A.OUTLINER)
    topic = ctx.store.read_json(A.STRATEGIST)
    assembled = assemble(
        edited_markdown=edited, brief=brief, topic=topic,
        internal_links=ctx.stores["internal_links"],
        site_name=ctx.cfg.site_name, base_url=ctx.cfg.blog_base_url,
        run_date=ctx.run_date,
        author_name=ctx.cfg.author_name, author_slug=ctx.cfg.author_slug,
        default_category=ctx.cfg.default_category)
    ctx.store.write_text(A.ASSEMBLER, assembled.markdown)
    ctx.store.write_json(A.ASSEMBLER_META, {
        "slug": assembled.slug,
        "leaked": bool(assembled.leaked),
        "leak_evidence": assembled.leak_evidence,
    })

    if getattr(assembled, "leaked", False):
        escalation_log(
            ctx.run_dir,
            f"confidentiality: scrubbed {len(assembled.leak_evidence)} "
            f"line(s) before publish")
        if ctx.deps.telegram:
            ctx.deps.telegram.send(
                f"В черновике найдены конфиденциальные маркеры — вычищены "
                f"перед публикацией ({len(assembled.leak_evidence)} стр.). "
                f"Проверь обработку evidence.", level="hard")


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
                                 or topic.get("topic", "post")),
                "leaked": False, "leak_evidence": []}

    assembled = SimpleNamespace(
        markdown=post_md, slug=meta["slug"],
        leaked=meta.get("leaked", False),
        leak_evidence=meta.get("leak_evidence", []))

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

    if ctx.deps.telegram and not ctx.dry_run:
        ctx.deps.telegram.send(
            _publish_report(ctx, brief, topic, status, post_md, stage),
            level="info")


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

    lines = [
        "Опубликована новая статья",
        "",
        f"📝 Тема: {title}",
        f"🔗 {status['url']}",
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

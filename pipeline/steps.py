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
from pipeline.quality import QualityError, require_publishable, require_review, require_final_review

from agents import editor, humanizer, outliner, researcher, strategist, writer


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
    research_cache: dict = field(default_factory=dict)


def model_for_stage(cfg, stage: int) -> str:
    key = STAGES[stage].model_key
    return cfg.model_opus if key == "opus" else cfg.model_sonnet


# --------------------------------------------------------------------------
# Shared helpers (also used by the full-run escalation loop)
# --------------------------------------------------------------------------
def gather_research_context(cfg, deps, spec, logger, cache=None):
    """Deterministic GSC/DataForSEO gathering with retry. On persistent
    API failure return empty + a flag so the ladder can move to a
    non-dependent stage instead of crashing."""
    cache = cache if cache is not None else {}
    gsc_rows: list = cache.get("gsc_rows", [])
    dfs_metrics: list = cache.get("dfs_metrics", [])
    api_failed = False

    if spec.use_gsc:
        try:
            # Stage 2 is "loosen GSC thresholds": widen the position band AND
            # drop the impressions floor. On a young blog the default floor of
            # 20 impressions leaves a single query, which starves the
            # Researcher and pushes it back onto the keyword reserve.
            stage_1 = spec.stage == 1
            min_pos, max_pos = (5.0, 20.0) if stage_1 else (3.0, 40.0)
            gsc_rows = deps.gsc.near_top_queries(
                min_pos=min_pos, max_pos=max_pos,
                min_impressions=20 if stage_1 else 5)
            cache["gsc_rows"] = gsc_rows
        except ClientError as e:
            if logger:
                logger.warning("GSC unavailable at stage %d: %s", spec.stage, e)
            api_failed = True

    if (spec.use_dataforseo and gsc_rows and deps.dataforseo is not None
            and not cache.get("dataforseo_failed")):
        try:
            seeds = [r["query"] for r in gsc_rows[:60]]
            dfs_metrics = deps.dataforseo.keyword_metrics(seeds)
            cache["dfs_metrics"] = dfs_metrics
        except ClientError as e:
            if logger:
                logger.warning("DataForSEO unavailable at stage %d: %s",
                               spec.stage, e)
            cache["dataforseo_failed"] = str(e)
            api_failed = not gsc_rows

    return gsc_rows, dfs_metrics, api_failed and not (gsc_rows or dfs_metrics)


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
        seed_topics=ctx.stores["seed_topics"],
        performance_context=ctx.stores.get("performance_context", ""))
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
        content_map=ctx.stores["content_map"],
        performance_context=ctx.stores.get("performance_context", ""))
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
        tools=["WebSearch"], max_tokens=ctx.cfg.agent_max_tokens,
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
    """Two editor passes and one stronger rewrite; never publish a failed review."""
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
            # Retry validation on the best draft, then use the stronger model.
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
        # Keep the best draft for review, but do not approve it by default.
        if ctx.logger:
            ctx.logger.warning("editor forced-final also failed validation "
                               "(%s) — retaining rejected draft", e)
        critique = {"checklist": {}, "passed": False,
                    "notes": f"forced-final validation failed: {e}"}
    critique["forced_final"] = True
    escalation_log(ctx.run_dir, "editor: forced Opus final rewrite after 2 fails")
    # Surface the extra pass in the run report even if it eventually passes.
    if ctx.logger:
        ctx.logger.warning("editor needed the final stronger-model pass")
    ctx.store.write_json(A.EDITOR_CRITIQUE, critique)
    if not critique.get("passed"):
        ctx.store.write_text("05-editor.rejected.md", current)
        raise QualityError("editor did not pass after three attempts; draft retained")
    require_review(current, critique)
    ctx.store.write_text(A.EDITOR_MD, current)


def step_uniqueness(ctx: StepContext) -> None:
    """#37 Deterministic near-duplicate guard between Editor and Assembler.

    Internal MinHash similarity of the edited body against already-published
    posts (bodies read from the blog clone, see ``published_corpus``).
    The score is persisted to 05b for telemetry/resume. Assembly/publication
    require this artifact and reject above-threshold results."""
    from pipeline import uniqueness as U

    body = ctx.store.read_text(A.EDITOR_MD)
    corpus = U.published_corpus(
        ctx.cfg.backlog_dir / "topic_history.json",
        blog_content_dir=(ctx.cfg.runs_dir / "_blog_repo"
                          / ctx.cfg.blog_posts_dir),
        exclude_prefix=f"{ctx.run_date}-")
    score, match = U.best_match(body, corpus)
    threshold = float(getattr(ctx.cfg, "uniqueness_threshold",
                              U.DEFAULT_THRESHOLD))

    result = {
        "max_similarity": round(score, 4),
        "threshold": threshold,
        "corpus_size": len(corpus),
        "provider": getattr(ctx.cfg, "uniqueness_provider", "internal"),
        "above_threshold": score >= threshold,
        "match_slug": (match or {}).get("slug", "") if match else "",
    }
    ctx.store.write_json(A.UNIQUENESS, result)

    if ctx.logger:
        if not corpus:
            # An empty corpus scores 0.0 for everything, i.e. the guard is
            # silently off — exactly how it sat idle for months. Say so.
            ctx.logger.warning(
                "uniqueness: corpus is empty (no published bodies under %s) "
                "— duplicate detection is INACTIVE this run",
                ctx.cfg.runs_dir / "_blog_repo" / ctx.cfg.blog_posts_dir)
        elif score >= threshold:
            # The downstream publication gate rejects this result.
            ctx.logger.warning(
                "uniqueness: body is highly similar (%.2f >= %.2f) to "
                "published post '%s' — review for paraphrase/self-repetition",
                score, threshold, result["match_slug"] or "(unknown)")
        else:
            ctx.logger.info(
                "uniqueness: max similarity %.2f (< %.2f) over %d published "
                "post(s) — ok", score, threshold, len(corpus))


def step_humanizer(ctx: StepContext) -> None:
    """De-AI the edited body (#39): deterministic cliché strip + an LLM
    rewrite anchored to the style guide. Failed review/preservation checks
    fall back to the approved editor body."""
    edited = ctx.store.read_text(A.EDITOR_MD)
    brief = ctx.store.read_json(A.OUTLINER)
    evidence = ensure_evidence(ctx, brief)
    body = humanizer.run(
        ctx.deps.agent_runner, model=ctx.cfg.model_opus, tools=[],
        max_tokens=ctx.cfg.agent_max_tokens, logger=ctx.logger,
        draft_md=edited, style_guide=ctx.stores["style_guide"],
        evidence_passages=evidence)
    if body != edited:
        # The last model to change the text must also pass editorial review.
        # A failed cosmetic pass can safely fall back to the approved original.
        try:
            result = editor.run(
                ctx.deps.agent_runner, model=ctx.cfg.model_sonnet, tools=[],
                max_tokens=ctx.cfg.agent_max_tokens, logger=ctx.logger,
                draft_md=body, brief=brief, style_guide=ctx.stores["style_guide"],
                evidence_passages=evidence, iteration=4, final=True)
            require_review(result["edited_markdown"], result["critique"])
            if humanizer.preservation_errors(edited, result["edited_markdown"]):
                raise QualityError("final edit changed protected article properties")
            body = result["edited_markdown"]
            ctx.store.write_json("05c-humanizer.critique.json", result["critique"])
        except (ClientError, QualityError, ValueError) as exc:
            if ctx.logger:
                ctx.logger.warning("final review rejected the stylistic rewrite (%s); "
                                   "keeping the approved editor version", exc)
            body = edited
    ctx.store.write_text(A.HUMANIZER, body)


def step_assembler(ctx: StepContext) -> None:
    """Deterministic build. Persists a meta sidecar (slug) so the
    Publisher needs nothing else."""
    require_publishable(ctx)
    edited = ctx.store.read_text(A.HUMANIZER)
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
    pending = (ctx.store.read_json(A.PUBLISHER)
               if ctx.store.exists(A.PUBLISHER) else {})
    if pending.get("status") != "pushed":
        require_publishable(ctx)
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
        published_keyword=brief.get("primary_keyword", ""),
        deployment_client=ctx.deps.deployment)
    ctx.store.write_json(A.PUBLISHER, status)
    # No per-publish message: the orchestrator emits one end-of-run fleet
    # report (+ internal report.json) instead, reusing build_digest/_publish_report.


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

    published_header = ("Статья опубликована, доступность подтверждена"
                        if status.get("deployment", {}).get("verified")
                        else "Статья отмечена опубликованной (без проверки доступности)")
    header = {"published": published_header,
              "pushed": "Изменения отправлены, деплой ещё не подтверждён"}.get(
                  status.get("status"), "Статья собрана")
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
    """Human-readable end-of-run summary: publish summary + degradations (#3)
    + token/$ usage (#5). Reused verbatim as the fleet run report's ``detailed``
    text (see build_run_report). Returns (text, level); ``level`` (hard on a
    forced-final, warn on any degradation, else info) is legacy and unused by
    the report path.
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
        # Цена статьи — человекочитаемый контекст; разбивка по токенам живёт в metrics.
        parts.append("")
        parts.append(f"💸 Стоимость статьи: ~${total.get('usd', 0):.2f} "
                     "(разбивка по токенам — в метриках отчёта)")

    return "\n".join(parts), level


def build_run_report(cfg, run_dir, run_date, accumulator, usage_report, *,
                     status, started_at, finished_at, run_id, trigger,
                     trigger_id, error=None,
                     animal="nightingale-seo-autoblog") -> dict:
    """Assemble the fleet ``ReportInput`` for this run (ok / fail / skipped).

    Reuses ``build_digest`` for the human ``detailed`` text and builds its own
    store + synthetic ctx, so it works even on the crash path (no StepContext
    exists yet). Short summaries belong exclusively to shepherd, so animals
    must not emit ``brief``. Does NOT emit ``schema_version`` / ``zoo`` /
    ``duration_ms`` — the fleet CLI fills those. ``metrics`` values are always
    numbers.
    """
    store = ArtifactStore(run_dir)
    pub = store.read_json(A.PUBLISHER) if store.exists(A.PUBLISHER) else {}
    topic = store.read_json(A.STRATEGIST) if store.exists(A.STRATEGIST) else {}
    stage = int(pub.get("escalation_stage", topic.get("_escalation_stage", 1)))
    ctx = SimpleNamespace(store=store, cfg=cfg, run_date=run_date, stage=stage)

    detailed = build_digest(ctx, accumulator, usage_report or {})[0]

    url = pub.get("url", "")
    degr = list(getattr(accumulator, "degradations", []))

    artifacts = [a for a in (url, pub.get("file", ""),
                             str(Path(run_dir) / "run.log")) if a]

    total = (usage_report or {}).get("total", {}) or {}
    metrics: dict = {
        "escalation_stage": stage,
        "degradations": len(degr),
        "errors": sum(1 for d in degr if d.get("level") == "ERROR"),
    }
    if total:
        metrics["cost_usd"] = round(float(total.get("usd", 0.0)), 6)
        metrics["input_tokens"] = int(total.get("input_tokens", 0))
        metrics["output_tokens"] = int(total.get("output_tokens", 0))
    bk = pub.get("backlog") or {}
    for src, dst in (("added", "backlog_added"), ("pruned", "backlog_pruned"),
                     ("kept", "backlog_kept")):
        if src in bk:
            metrics[dst] = int(bk[src])
    if store.exists(A.UNIQUENESS):
        try:
            metrics["uniqueness_max_similarity"] = float(
                store.read_json(A.UNIQUENESS).get("max_similarity", 0.0))
        except (ValueError, OSError):
            pass

    return {
        "animal": animal,
        "run_id": run_id,
        "trigger": trigger,
        "trigger_id": trigger_id,
        "attempt": 1,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "error": error,
        "detailed": detailed,
        "artifacts": artifacts,
        "metrics": metrics,
    }


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
    Step("uniqueness", (A.EDITOR_MD,), A.UNIQUENESS, step_uniqueness),
    Step("humanizer", (A.EDITOR_MD, A.OUTLINER), A.HUMANIZER, step_humanizer),
    Step("assembler", (A.HUMANIZER, A.OUTLINER, A.STRATEGIST), A.ASSEMBLER,
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
    if ctx.force and ctx.store.exists(A.PUBLISHER):
        if ctx.store.read_json(A.PUBLISHER).get("status") in {"pushed", "published"}:
            raise QualityError("cannot force-rewrite a committed publication; use a new run")
    for step in STEPS:
        if step.name not in chosen:
            continue

        can_resume = ctx.store.exists(step.output) and not ctx.force
        if can_resume and step.name == "publisher":
            status = ctx.store.read_json(A.PUBLISHER).get("status")
            can_resume = status == "published" or (ctx.dry_run and status == "dry_run")
        if can_resume and step.name == "editor":
            # A body without an approving critique is not a completed edit.
            can_resume = ctx.store.exists(A.EDITOR_CRITIQUE)
            if can_resume:
                try:
                    require_review(ctx.store.read_text(A.EDITOR_MD),
                                   ctx.store.read_json(A.EDITOR_CRITIQUE))
                except (ValueError, QualityError):
                    can_resume = False
        if can_resume and step.name == "humanizer":
            try:
                require_final_review(ctx)
            except (OSError, ValueError, QualityError):
                can_resume = False
        if can_resume:
            if ctx.logger:
                ctx.logger.info("skip %s: %s already present (resume)",
                                step.name, step.output)
            continue

        # Recomputing an upstream artifact invalidates every dependent output,
        # including outputs outside a manually selected step subset.
        for downstream in STEPS[STEPS.index(step) + 1:]:
            ctx.store.path(downstream.output).unlink(missing_ok=True)
        if STEPS.index(step) <= STEP_NAMES.index("outliner"):
            ctx.store.path(A.EVIDENCE).unlink(missing_ok=True)
        if STEPS.index(step) <= STEP_NAMES.index("editor"):
            ctx.store.path(A.EDITOR_CRITIQUE).unlink(missing_ok=True)
        if STEPS.index(step) <= STEP_NAMES.index("humanizer"):
            ctx.store.path("05c-humanizer.critique.json").unlink(missing_ok=True)
        if STEPS.index(step) <= STEP_NAMES.index("assembler"):
            ctx.store.path(A.ASSEMBLER_META).unlink(missing_ok=True)

        for inp in step.inputs:
            if not ctx.store.exists(inp):
                hint = _prev_step(step)
                raise StepInputError(
                    f"cannot run '{step.name}': missing input {inp}"
                    + (f" — run '{hint}' first" if hint else ""))

        # Tag every line this step emits (incl. CLIAgentRunner's "agent ->
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

"""The deterministic orchestrator.

Two entry points share one set of step functions (``pipeline.steps``):

* ``run_pipeline`` — the no-flag full daily run. Owns the escalation
  ladder (steps 1–2) and quality checks, then drives the remaining steps.
* ``run_selected_steps`` — runs an explicit subset of steps in isolation
  (single pass at ``start_stage``, no auto-escalation).

Dependency injection: both take ``deps=PipelineDeps`` so tests swap the
agent runner and external clients for fakes (no network/SDK needed).
"""

from __future__ import annotations

import json
import secrets
import sys
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from logging_setup import escalation_log, get_agent_logger, setup_run_logging
from pipeline import artifacts as A
from pipeline import dedupe
from pipeline import steps as S
from pipeline.artifacts import ArtifactStore
from pipeline.escalation import SCORE_THRESHOLD, EscalationLadder
from pipeline.steps import (StepContext, gather_research_context,
                            researcher_pass, run_steps, strategist_pass)
from clients.websearch import tools_for_stage
from clients.retry import ClientError, PermanentClientError
from pipeline.quality import QualityError, topic_rejection


@dataclass
class PipelineDeps:
    agent_runner: object
    gsc: object
    dataforseo: object
    evidence: object
    git: object
    telegram: object      # fatal-crash-only alert (see run_pipeline except)
    fleet: object         # ark-agent-fleet run report (primary result channel)
    deployment: object = None


def default_deps(cfg, logger) -> PipelineDeps:
    from clients.dataforseo import DataForSEOClient
    from clients.evidence import EvidenceClient
    from clients.fleet import FleetClient
    from clients.git_client import GitClient
    from clients.gsc import GSCClient
    from clients.deployment import DeploymentClient
    from clients.telegram import TelegramClient

    from agents.runner import CLIAgentRunner

    return PipelineDeps(
        agent_runner=CLIAgentRunner(codex_bin=cfg.codex_bin,
                                    timeout=cfg.codex_timeout),
        gsc=GSCClient(cfg.gsc_service_account_json, cfg.gsc_site_url, logger),
        dataforseo=(DataForSEOClient(cfg.dataforseo_login,
                                    cfg.dataforseo_password, logger)
                    if cfg.dataforseo_login and cfg.dataforseo_password else None),
        evidence=EvidenceClient(cfg.evidence_dir, logger),
        git=GitClient(cfg.blog_repo_url, cfg.git_deploy_key, cfg.blog_branch,
                      cfg.runs_dir / "_blog_repo", logger, node_bin=cfg.node_bin),
        telegram=TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id,
                                logger),
        fleet=FleetClient(cfg.ark_repo, cfg.ark_zoo, no_sync=cfg.ark_no_sync,
                          node_bin=cfg.node_bin, logger=logger),
        deployment=DeploymentClient(timeout=cfg.deployment_timeout),
    )


def _load_store(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _load_stores(cfg) -> dict:
    from pipeline.performance import planning_context, read_latest
    return {
        "performance_context": planning_context(read_latest(cfg.performance_dir)),
        "backlog": _load_store(cfg.backlog_dir / "keyword_backlog.json",
                               {"candidates": []}),
        "topic_history": _load_store(
            cfg.backlog_dir / "topic_history.json", {"published": []}),
        "seed_topics": _load_text(cfg.backlog_dir / "seed_topics.md"),
        "content_map": _load_text(cfg.themes_dir / "content_map.md"),
        "internal_links": _load_store(
            cfg.themes_dir / "internal_links.json", {"posts": []}),
        "style_guide": _load_text(cfg.style_guide_path),
    }


def _build_context(cfg, deps, run_date, *, dry_run, stage, force, logger):
    run_dir = cfg.runs_dir / run_date
    return StepContext(
        cfg=cfg, deps=deps, store=ArtifactStore(run_dir),
        stores=_load_stores(cfg), run_dir=run_dir, run_date=run_date,
        dry_run=dry_run, stage=stage, force=force, logger=logger)


def _refresh_catalog(ctx):
    """Include externally published posts when choosing topics and links."""
    from pipeline.performance import post_catalog
    posts_dir = ctx.cfg.runs_dir / "_blog_repo" / ctx.cfg.blog_posts_dir
    if not posts_dir.is_dir():
        return
    catalog = post_catalog(posts_dir, ctx.cfg.blog_base_url)
    history = ctx.stores["topic_history"].setdefault("published", [])
    links = ctx.stores["internal_links"].setdefault("posts", [])
    history_slugs = {p.get("slug") for p in history}
    link_slugs = {p.get("slug") for p in links}
    for post in catalog:
        if post["slug"] not in history_slugs:
            history.append({**post, "topic": post["title"], "keyword": post["title"]})
        if post["slug"] not in link_slugs:
            links.append(post)


def _research_and_select(ctx: StepContext, ladder: EscalationLadder):
    """Full-run steps 1–2 with the escalation ladder. Resumes from
    artifacts; otherwise loops Researcher+Strategist, escalating while the
    Strategist's score is below threshold (and through dead-API stages)."""
    store = ctx.store
    if store.exists(A.STRATEGIST) and store.exists(A.RESEARCHER):
        topic = store.read_json(A.STRATEGIST)
        if not topic_rejection(topic, ctx.stores["topic_history"]):
            ladder.stage = int(topic.get("_escalation_stage", ladder.stage))
            ctx.stage = ladder.stage
            ctx.logger.info("resume: approved steps 1–2 (stage %s)", ladder.stage)
            return store.read_json(A.RESEARCHER), topic
        # A failed selection must not be accepted by a subsequent resume.
        for step in S.STEPS:
            store.path(step.output).unlink(missing_ok=True)
        for name in (A.EVIDENCE, A.EDITOR_CRITIQUE, A.ASSEMBLER_META):
            store.path(name).unlink(missing_ok=True)

    topic: dict = {"score": 0.0}
    while True:
        spec = ladder.spec
        model = ladder.model_id()
        tools = tools_for_stage(spec.stage) if spec.use_websearch else []
        ctx.logger.info("stage %d (%s) :: %s", spec.stage, spec.model_key,
                        spec.approach)

        gsc_rows, dfs_metrics, api_failed = gather_research_context(
            ctx.cfg, ctx.deps, spec, ctx.logger, ctx.research_cache)
        store.write_json(A.RESEARCH_CONTEXT, ctx.research_cache)

        if api_failed and not ladder.at_guarantee() and spec.stage <= 2:
            ladder.escalate("required API unavailable")
            continue

        ctx.stage = ladder.stage
        orch_logger = ctx.logger
        try:
            ctx.logger = get_agent_logger("researcher")
            researcher_pass(ctx, spec, model, tools, gsc_rows, dfs_metrics)
            ctx.logger = get_agent_logger("strategist")
            topic = strategist_pass(ctx, model, ladder.stage)
        except PermanentClientError:
            raise
        except ClientError as exc:
            if ladder.escalate(f"agent failed after retries: {exc}"):
                continue
            raise
        finally:
            ctx.logger = orch_logger

        score = float(topic.get("score", 0.0))
        ctx.logger.info("strategist score=%.3f threshold=%.2f",
                        score, SCORE_THRESHOLD)

        # A high score says nothing about whether we already covered this.
        # The text-level guard (uniqueness) cannot help here: same-topic
        # posts written from scratch share almost no phrasing. Compare the
        # topic itself, and spend an escalation stage looking for another.
        reason = topic_rejection(topic, ctx.stores["topic_history"])
        if not reason:
            break
        if not ladder.escalate(reason):
            raise QualityError(f"no publishable topic at escalation ceiling: {reason}")

    return store.read_json(A.RESEARCHER), topic


def run_pipeline(cfg, *, run_date: str, dry_run: bool = False,
                 start_stage: int = 1, deps: PipelineDeps | None = None) -> int:
    run_dir = cfg.runs_dir / run_date
    accumulator = setup_run_logging(run_dir)
    logger = get_agent_logger("orchestrator")

    started_at = datetime.now(timezone.utc).isoformat()
    run_id = f"{started_at}-{cfg.ark_animal}-{secrets.token_hex(3)}"
    trigger_id = f"cron:{run_date}"

    logger.info("=== run start date=%s dry_run=%s ===", run_date, dry_run)

    # Resolve deps before the idempotency check (all client constructors are
    # side-effect-free) so a no-op day can still emit a `skipped` report — the
    # fleet contract is "a report on every run".
    if deps is None:
        deps = default_deps(cfg, logger)

    if ArtifactStore(run_dir).is_published():
        logger.info("idempotent no-op: already published for %s", run_date)
        report = S.build_run_report(
            cfg, run_dir, run_date, accumulator, {}, status="skipped",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            run_id=run_id, trigger="cron", trigger_id=trigger_id,
            animal=cfg.ark_animal)
        _deliver_report(deps, run_dir, report, submit=True, dry_run=dry_run,
                        logger=logger)
        return 0

    try:
        ctx = _build_context(cfg, deps, run_date, dry_run=dry_run,
                             stage=start_stage, force=False, logger=logger)
        ladder = EscalationLadder(cfg, run_dir, logger,
                                  start_stage=start_stage)

        pending = (ctx.store.read_json(A.PUBLISHER)
                   if ctx.store.exists(A.PUBLISHER) else {})
        if pending.get("status") == "pushed":
            run_steps(ctx, ["publisher"])
        else:
            deps.git.ensure_clone()
            _refresh_catalog(ctx)
            if not dry_run and hasattr(deps.gsc, "analytics"):
                from pipeline.performance import refresh_report, planning_context
                try:
                    report = refresh_report(cfg, deps.gsc,
                        deps.git.repo_path / cfg.blog_posts_dir, today=date.today())
                    ctx.stores["performance_context"] = planning_context(report)
                except Exception as exc:
                    logger.warning("SEO performance report unavailable; retaining last report: %s", exc)
            _research_and_select(ctx, ladder)
            ctx.stage = ladder.stage
            run_steps(ctx, ["outliner", "writer", "editor", "uniqueness",
                            "humanizer", "assembler", "publisher"])

        status = ctx.store.read_json(A.PUBLISHER)
        logger.info("=== run complete status=%s ===", status["status"])
        _finalize_run(ctx, deps, run_dir, accumulator, started_at=started_at,
                      run_id=run_id, trigger="cron", trigger_id=trigger_id,
                      status="ok", submit=True)
        return 0

    except Exception as exc:  # noqa: BLE001
        logger.exception("pipeline failed: %s", exc)
        escalation_log(run_dir, f"FATAL: {exc}")
        logger.info("%s", accumulator.summary_block())
        try:
            from pipeline import usage as U
            records = (list(getattr(deps.agent_runner, "records", []) or [])
                       if deps else [])
            usage_report = U.summarize(records, cfg.model_prices)
        except Exception:  # noqa: BLE001
            usage_report = {}
        report = S.build_run_report(
            cfg, run_dir, run_date, accumulator, usage_report, status="fail",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            run_id=run_id, trigger="cron", trigger_id=trigger_id,
            error=traceback.format_exc(), animal=cfg.ark_animal)
        _deliver_report(deps, run_dir, report, submit=True, dry_run=dry_run,
                        logger=logger)
        # Last-resort out-of-band ping: if the ark/VPS itself is down the fleet
        # report may not deliver, so a fatal crash still pings Telegram.
        try:
            if not dry_run and deps and getattr(deps, "telegram", None):
                deps.telegram.send(
                    f"Пайплайн упал на {run_date}: {str(exc)[:1500]}",
                    level="hard")
        except Exception:  # noqa: BLE001
            pass
        return 1


def _deliver_report(deps, run_dir, report, *, submit, dry_run, logger) -> None:
    """Write the machine-readable run summary to runs/<date>/report.json and
    (for autonomous runs) submit it to the fleet. Both are fail-soft."""
    try:
        ArtifactStore(run_dir).write_json(A.RUN_REPORT, report)
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger.warning("report.json write failed: %s", exc)
    if submit and getattr(deps, "fleet", None):
        deps.fleet.submit(report, dry_run=dry_run)


def _finalize_run(ctx, deps, run_dir, accumulator, *, started_at, run_id,
                  trigger, trigger_id, status="ok", error=None,
                  submit=True) -> None:
    """End-of-run telemetry (#3/#5): write usage.json, append the degradation
    summary to run.log, build the run report, persist it to report.json, and
    (if submit) send it to the fleet. Never raises — finalization must not turn
    a published run into a failure.
    """
    from pipeline import usage as U

    try:
        records = list(getattr(deps.agent_runner, "records", []) or [])
        usage_report = U.summarize(records, ctx.cfg.model_prices)
        ctx.store.write_json("usage.json", usage_report)
    except Exception as exc:  # noqa: BLE001
        ctx.logger.warning("usage accounting failed: %s", exc)
        usage_report = {}

    ctx.logger.info("%s", accumulator.summary_block())

    try:
        report = S.build_run_report(
            ctx.cfg, run_dir, ctx.run_date, accumulator, usage_report,
            status=status, started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            run_id=run_id, trigger=trigger, trigger_id=trigger_id, error=error,
            animal=ctx.cfg.ark_animal)
        _deliver_report(deps, run_dir, report, submit=submit,
                        dry_run=ctx.dry_run, logger=ctx.logger)
    except Exception as exc:  # noqa: BLE001
        ctx.logger.warning("run report failed: %s", exc)


def run_selected_steps(cfg, *, run_date: str, step_names: list[str],
                       dry_run: bool = False, start_stage: int = 1,
                       force: bool = False,
                       deps: PipelineDeps | None = None) -> int:
    """Run an explicit subset of steps in isolation (single pass at
    ``start_stage``, no auto-escalation)."""
    run_dir = cfg.runs_dir / run_date
    accumulator = setup_run_logging(run_dir)
    logger = get_agent_logger("orchestrator")
    started_at = datetime.now(timezone.utc).isoformat()
    run_id = f"{started_at}-{cfg.ark_animal}-{secrets.token_hex(3)}"
    logger.info("=== selected steps %s date=%s dry_run=%s stage=%d ===",
                step_names, run_date, dry_run, start_stage)

    if deps is None:
        deps = default_deps(cfg, logger)

    try:
        ctx = _build_context(cfg, deps, run_date, dry_run=dry_run,
                             stage=start_stage, force=force, logger=logger)
        if {"researcher", "strategist", "uniqueness"}.intersection(step_names):
            deps.git.ensure_clone()
            _refresh_catalog(ctx)
        run_steps(ctx, step_names)
        logger.info("=== selected steps complete ===")
        # Same end-of-run telemetry as a full run, but manual: write the
        # internal report.json (trigger="manual") WITHOUT submitting to the
        # shared fleet journal — that reflects the autonomous cron run only.
        _finalize_run(ctx, deps, run_dir, accumulator, started_at=started_at,
                      run_id=run_id, trigger="manual", trigger_id="",
                      status="ok", submit=False)
        return 0
    except S.StepInputError as exc:
        logger.error("%s", exc)
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.exception("step run failed: %s", exc)
        escalation_log(run_dir, f"FATAL (selected steps): {exc}")
        return 1

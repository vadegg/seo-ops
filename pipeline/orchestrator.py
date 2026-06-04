"""The deterministic orchestrator.

Two entry points share one set of step functions (``pipeline.steps``):

* ``run_pipeline`` — the no-flag full daily run. Owns the escalation
  ladder (steps 1–2) and the publish guarantee, then drives steps 3–7.
* ``run_selected_steps`` — runs an explicit subset of steps in isolation
  (single pass at ``start_stage``, no auto-escalation).

Dependency injection: both take ``deps=PipelineDeps`` so tests swap the
agent runner and external clients for fakes (no network/SDK needed).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from logging_setup import escalation_log, get_agent_logger, setup_run_logging
from pipeline import artifacts as A
from pipeline import steps as S
from pipeline.artifacts import ArtifactStore
from pipeline.escalation import SCORE_THRESHOLD, EscalationLadder
from pipeline.steps import (StepContext, gather_research_context,
                            researcher_pass, run_steps, strategist_pass)
from clients.websearch import tools_for_stage


@dataclass
class PipelineDeps:
    agent_runner: object
    gsc: object
    dataforseo: object
    evidence: object
    git: object
    telegram: object


def default_deps(cfg, logger) -> PipelineDeps:
    from clients.dataforseo import DataForSEOClient
    from clients.evidence import EvidenceClient
    from clients.git_client import GitClient
    from clients.gsc import GSCClient
    from clients.telegram import TelegramClient

    from agents.runner import SDKAgentRunner

    return PipelineDeps(
        agent_runner=SDKAgentRunner(cfg.anthropic_api_key),
        gsc=GSCClient(cfg.gsc_service_account_json, cfg.gsc_site_url, logger),
        dataforseo=DataForSEOClient(cfg.dataforseo_login,
                                    cfg.dataforseo_password, logger),
        evidence=EvidenceClient(cfg.evidence_dir, logger),
        git=GitClient(cfg.blog_repo_url, cfg.git_deploy_key, cfg.blog_branch,
                      cfg.runs_dir / "_blog_repo", logger),
        telegram=TelegramClient(cfg.telegram_bot_token, cfg.telegram_chat_id,
                                logger),
    )


def _load_store(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _load_stores(cfg) -> dict:
    return {
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


def _research_and_select(ctx: StepContext, ladder: EscalationLadder):
    """Full-run steps 1–2 with the escalation ladder. Resumes from
    artifacts; otherwise loops Researcher+Strategist, escalating while the
    Strategist's score is below threshold (and through dead-API stages)."""
    store = ctx.store
    if store.exists(A.STRATEGIST) and store.exists(A.RESEARCHER):
        topic = store.read_json(A.STRATEGIST)
        ladder.stage = int(topic.get("_escalation_stage", ladder.stage))
        ctx.stage = ladder.stage
        ctx.logger.info("resume: steps 1–2 from artifacts (stage %s)",
                        ladder.stage)
        return store.read_json(A.RESEARCHER), topic

    topic: dict = {"score": 0.0}
    while True:
        spec = ladder.spec
        model = ladder.model_id()
        tools = tools_for_stage(spec.stage) if spec.use_websearch else []
        ctx.logger.info("stage %d (%s) :: %s", spec.stage, spec.model_key,
                        spec.approach)

        gsc_rows, dfs_metrics, api_failed = gather_research_context(
            ctx.cfg, ctx.deps, spec, ctx.logger)

        if api_failed and not ladder.at_guarantee() and spec.stage <= 2:
            ladder.escalate("required API unavailable")
            continue

        ctx.stage = ladder.stage
        researcher_pass(ctx, spec, model, tools, gsc_rows, dfs_metrics)
        topic = strategist_pass(ctx, model, ladder.stage)

        score = float(topic.get("score", 0.0))
        ctx.logger.info("strategist score=%.3f threshold=%.2f",
                        score, SCORE_THRESHOLD)

        if score >= SCORE_THRESHOLD or ladder.at_guarantee():
            break
        if not ladder.escalate(f"topic score {score:.2f} below threshold"):
            break  # ceiling reached — accept best available

    return store.read_json(A.RESEARCHER), topic


def run_pipeline(cfg, *, run_date: str, dry_run: bool = False,
                 start_stage: int = 1, deps: PipelineDeps | None = None) -> int:
    run_dir = cfg.runs_dir / run_date
    setup_run_logging(run_dir)
    logger = get_agent_logger("orchestrator")

    logger.info("=== run start date=%s dry_run=%s ===", run_date, dry_run)

    if ArtifactStore(run_dir).is_published():
        logger.info("idempotent no-op: already published for %s", run_date)
        return 0

    if deps is None:
        deps = default_deps(cfg, logger)

    try:
        ctx = _build_context(cfg, deps, run_date, dry_run=dry_run,
                             stage=start_stage, force=False, logger=logger)
        ladder = EscalationLadder(cfg, run_dir, deps.telegram, logger,
                                  start_stage=start_stage)

        _research_and_select(ctx, ladder)
        ctx.stage = ladder.stage

        run_steps(ctx, ["outliner", "writer", "editor",
                        "assembler", "publisher"])

        status = ctx.store.read_json(A.PUBLISHER)
        logger.info("=== run complete status=%s ===", status["status"])
        return 0

    except Exception as exc:  # noqa: BLE001
        logger.exception("pipeline failed: %s", exc)
        escalation_log(run_dir, f"FATAL: {exc}")
        try:
            if deps and deps.telegram:
                deps.telegram.send(
                    f"Пайплайн упал на {run_date}.\n\n"
                    f"Ошибка: {str(exc)[:1500]}", level="hard")
                deps.telegram.send_document(
                    run_dir / "run.log",
                    caption=f"Лог прогона {run_date}", level="hard")
        except Exception:  # noqa: BLE001
            pass
        return 1


def run_selected_steps(cfg, *, run_date: str, step_names: list[str],
                       dry_run: bool = False, start_stage: int = 1,
                       force: bool = False,
                       deps: PipelineDeps | None = None) -> int:
    """Run an explicit subset of steps in isolation (single pass at
    ``start_stage``, no auto-escalation)."""
    run_dir = cfg.runs_dir / run_date
    setup_run_logging(run_dir)
    logger = get_agent_logger("orchestrator")
    logger.info("=== selected steps %s date=%s dry_run=%s stage=%d ===",
                step_names, run_date, dry_run, start_stage)

    if deps is None:
        deps = default_deps(cfg, logger)

    try:
        ctx = _build_context(cfg, deps, run_date, dry_run=dry_run,
                             stage=start_stage, force=force, logger=logger)
        run_steps(ctx, step_names)
        logger.info("=== selected steps complete ===")
        return 0
    except S.StepInputError as exc:
        logger.error("%s", exc)
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        logger.exception("step run failed: %s", exc)
        escalation_log(run_dir, f"FATAL (selected steps): {exc}")
        return 1

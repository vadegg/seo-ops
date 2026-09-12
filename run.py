"""Entry point / deterministic orchestrator launcher.

Daily flow (no flags = full pipeline, escalation + publish guarantee):
    python run.py                 # real run, publishes via git push
    python run.py --dry-run       # everything except git push
    python run.py --date 2026-05-19   # re-run a specific day (resume)

Per-agent runs (single pass at --start-stage, no auto-escalation):
    python run.py --list-steps
    python run.py --steps researcher --dry-run
    python run.py --steps strategist --dry-run        # needs 01 on disk
    python run.py --from outliner --dry-run           # outliner..publisher
    python run.py --stop-after writer --dry-run       # researcher..writer
    python run.py --steps editor --force --dry-run    # re-run even if 05 exists

The systemd timer invokes `python run.py` once a day. Idempotency
(see pipeline/orchestrator.py) makes manual re-runs safe.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from config import Config, ConfigError
from pipeline.steps import STEP_NAMES


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="seo-autoblog")
    p.add_argument("--dry-run", action="store_true",
                   help="run the full pipeline but do not git push")
    p.add_argument("--date", default=None,
                   help="run date YYYY-MM-DD (default: today in configured TZ)")
    p.add_argument("--max-stage", type=int, default=None,
                   help="override escalation ceiling for this run")
    p.add_argument("--start-stage", type=int, default=1,
                   help="escalation stage to start at / use for single steps")

    # Per-step selection (any of these switches to selected-steps mode).
    p.add_argument("--list-steps", action="store_true",
                   help="print the pipeline step names and exit")
    p.add_argument("--report-only", action="store_true",
                   help="refresh local SEO report using read-only GSC APIs; no agents or publication")
    p.add_argument("--inspect-limit", type=int, default=20,
                   help="maximum URL inspections for --report-only (0..1000)")
    p.add_argument("--blog-dir", default=None,
                   help="existing blog checkout for --report-only (default: managed clone)")
    p.add_argument("--steps", default=None,
                   help="comma-separated step names to run in isolation")
    p.add_argument("--from", dest="from_step", default=None,
                   help="run from this step to the end of the pipeline")
    p.add_argument("--stop-after", dest="stop_after", default=None,
                   help="run up to and including this step")
    p.add_argument("--force", action="store_true",
                   help="re-run a step even if its output artifact exists")
    return p.parse_args(argv)


def _validate(name: str) -> str:
    if name not in STEP_NAMES:
        raise SystemExit(f"unknown step '{name}'. valid: {', '.join(STEP_NAMES)}")
    return name


def _resolve_steps(args: argparse.Namespace) -> list[str] | None:
    """Return the explicit step subset, or None for a full run."""
    if not (args.steps or args.from_step or args.stop_after):
        return None
    if args.steps:
        return [_validate(s.strip()) for s in args.steps.split(",") if s.strip()]
    start = STEP_NAMES.index(_validate(args.from_step)) if args.from_step else 0
    end = (STEP_NAMES.index(_validate(args.stop_after))
           if args.stop_after else len(STEP_NAMES) - 1)
    if start > end:
        raise SystemExit("--from step comes after --stop-after step")
    return STEP_NAMES[start:end + 1]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.list_steps:
        print("\n".join(STEP_NAMES))
        return 0

    selected = _resolve_steps(args)
    if args.report_only and (selected or args.force or args.dry_run):
        raise SystemExit("--report-only cannot be combined with step selection, --force or --dry-run")
    if not 0 <= args.inspect_limit <= 1000:
        raise SystemExit("--inspect-limit must be between 0 and 1000")

    try:
        cfg = Config.load()
    except ConfigError as e:
        # Early, loud failure — before any agent or API call.
        print(str(e), file=sys.stderr)
        return 2

    if args.max_stage is not None:
        if not 1 <= args.max_stage <= 4:
            raise SystemExit("--max-stage must be between 1 and 4")
        cfg = type(cfg)(**{**cfg.__dict__, "max_stage": args.max_stage})
    if not 1 <= args.start_stage <= cfg.max_stage:
        raise SystemExit("--start-stage must be between 1 and --max-stage")

    run_date = args.date or datetime.now(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d")
    try:
        if datetime.strptime(run_date, "%Y-%m-%d").strftime("%Y-%m-%d") != run_date:
            raise ValueError
    except ValueError:
        raise SystemExit("--date must be a calendar date in YYYY-MM-DD format")

    # Imported here so a bad config fails before importing the pipeline.
    from pipeline.orchestrator import run_pipeline, run_selected_steps

    from pipeline.locking import AlreadyRunning, run_lock
    try:
        with run_lock(cfg.runs_dir):
            if args.report_only:
                from datetime import date
                from pathlib import Path
                from clients.gsc import GSCClient
                from pipeline.performance import refresh_report
                repo = Path(args.blog_dir) if args.blog_dir else cfg.runs_dir / "_blog_repo"
                posts = repo / cfg.blog_posts_dir
                if not posts.is_dir():
                    raise SystemExit(f"blog posts directory does not exist: {posts}")
                gsc = GSCClient(cfg.gsc_service_account_json, cfg.gsc_site_url)
                report = refresh_report(cfg, gsc, posts, today=date.fromisoformat(run_date),
                                        force=True, inspect_limit=args.inspect_limit)
                print(cfg.performance_dir / f"{report['generated_on']}.md")
                return 0
            if selected is None:
                return run_pipeline(cfg, run_date=run_date, dry_run=args.dry_run,
                                    start_stage=args.start_stage)
            return run_selected_steps(
                cfg, run_date=run_date, step_names=selected, dry_run=args.dry_run,
                start_stage=args.start_stage, force=args.force)
    except AlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        return 75


if __name__ == "__main__":
    raise SystemExit(main())

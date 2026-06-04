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

    try:
        cfg = Config.load()
    except ConfigError as e:
        # Early, loud failure — before any agent or API call.
        print(str(e), file=sys.stderr)
        return 2

    if args.max_stage is not None:
        cfg = type(cfg)(**{**cfg.__dict__, "max_stage": args.max_stage})

    run_date = args.date or datetime.now(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d")

    # Imported here so a bad config fails before importing the pipeline.
    from pipeline.orchestrator import run_pipeline, run_selected_steps

    if selected is None:
        return run_pipeline(
            cfg, run_date=run_date, dry_run=args.dry_run,
            start_stage=args.start_stage)

    return run_selected_steps(
        cfg, run_date=run_date, step_names=selected, dry_run=args.dry_run,
        start_stage=args.start_stage, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())

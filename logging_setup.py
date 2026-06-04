"""Run-scoped logging.

Every agent/code step logs to a shared per-run ``run.log`` with the
format ``timestamp | agent | level | message``. ``escalation.log`` gets
a terse one-line summary of every ladder transition. journald (systemd)
sits on top of stderr.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_LOG_FORMAT = "%(asctime)s | %(agent)s | %(levelname)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


class _AgentFilter(logging.Filter):
    """Guarantees every record has an ``agent`` field for the format string."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        if not hasattr(record, "agent"):
            record.agent = "orchestrator"
        return True


def setup_run_logging(run_dir: Path) -> logging.Logger:
    """Configure the root 'seo-autoblog' logger for one run.

    Writes to ``<run_dir>/run.log`` and stderr (captured by journald).
    Idempotent: re-configuring the same run replaces handlers.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("seo-autoblog")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    flt = _AgentFilter()

    file_h = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
    file_h.setFormatter(fmt)
    file_h.addFilter(flt)
    logger.addHandler(file_h)

    stream_h = logging.StreamHandler(sys.stderr)
    stream_h.setFormatter(fmt)
    stream_h.addFilter(flt)
    logger.addHandler(stream_h)

    return logger


def get_agent_logger(name: str) -> logging.LoggerAdapter:
    """Logger that tags every line with the agent/step name."""
    base = logging.getLogger("seo-autoblog")
    return logging.LoggerAdapter(base, {"agent": name})


def escalation_log(run_dir: Path, message: str) -> None:
    """Append a terse summary line to ``escalation.log``."""
    run_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with (run_dir / "escalation.log").open("a", encoding="utf-8") as fh:
        fh.write(f"{ts} | {message}\n")

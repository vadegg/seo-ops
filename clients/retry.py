"""Shared retry/backoff helper used by every external client."""

from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class ClientError(RuntimeError):
    """External client failed after exhausting retries."""


def with_backoff(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    logger=None,
    label: str = "request",
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn`` with exponential backoff + jitter.

    Raises :class:`ClientError` wrapping the last exception when all
    attempts fail, so callers can branch to a non-dependent escalation
    stage instead of crashing the run.
    """
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - clients raise varied types
            last = exc
            if logger is not None:
                logger.warning(
                    "%s failed (attempt %d/%d): %s", label, attempt, attempts, exc
                )
            if attempt == attempts:
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay *= 0.5 + random.random()  # jitter 0.5x–1.5x
            sleep(delay)
    raise ClientError(f"{label} failed after {attempts} attempts: {last}") from last

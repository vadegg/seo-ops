"""One writer at a time across all dates: the blog checkout is shared."""

from contextlib import contextmanager
import fcntl
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def run_lock(runs_dir: Path):
    runs_dir.mkdir(parents=True, exist_ok=True)
    with (runs_dir / ".run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AlreadyRunning("another SEO run is using the shared checkout") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)

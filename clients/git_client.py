"""Thin git wrapper around the blog repo (clone, write, commit, push).

Uses an isolated SSH deploy key via GIT_SSH_COMMAND so it never touches
the operator's ssh-agent or global git config.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .retry import with_backoff


class GitClient:
    def __init__(
        self,
        repo_url: str,
        deploy_key: Path,
        branch: str,
        workdir: Path,
        logger=None,
    ):
        self._url = repo_url
        self._key = Path(deploy_key)
        self._branch = branch
        self._workdir = Path(workdir)
        self._log = logger

    @property
    def repo_path(self) -> Path:
        return self._workdir

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {self._key} -o IdentitiesOnly=yes "
            f"-o StrictHostKeyChecking=accept-new"
        )
        return env

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["git", *args]
        if self._log:
            self._log.info("git %s", " ".join(args))
        return subprocess.run(
            cmd,
            cwd=str(self._workdir) if self._workdir.exists() else None,
            env=self._env(),
            check=check,
            capture_output=True,
            text=True,
        )

    def ensure_clone(self) -> None:
        """Clone if absent, otherwise fetch + hard-reset to remote branch."""
        if (self._workdir / ".git").is_dir():
            self._git("fetch", "origin", self._branch)
            self._git("checkout", self._branch)
            self._git("reset", "--hard", f"origin/{self._branch}")
            return
        self._workdir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--branch", self._branch, "--single-branch",
             self._url, str(self._workdir)],
            env=self._env(), check=True, capture_output=True, text=True,
        )

    def write_post(self, rel_path: str, content: str) -> Path:
        dest = self._workdir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        return dest

    def commit_and_push(self, rel_paths: list[str], message: str,
                        *, push: bool = True) -> str:
        """Stage given paths, commit, optionally push. Returns commit sha.

        If there is nothing to commit (idempotent re-run), returns the
        existing HEAD sha without creating an empty commit.
        """
        for rp in rel_paths:
            self._git("add", rp)
        status = self._git("status", "--porcelain")
        if not status.stdout.strip():
            head = self._git("rev-parse", "HEAD")
            return head.stdout.strip()

        self._git(
            "-c", "user.name=seo-autoblog",
            "-c", "user.email=bot@seo-autoblog.local",
            "commit", "-m", message,
        )
        sha = self._git("rev-parse", "HEAD").stdout.strip()
        if push:
            with_backoff(
                lambda: self._git("push", "origin", self._branch),
                attempts=4, logger=self._log, label="git push",
            )
        elif self._log:
            self._log.info("dry-run: skipping git push (commit %s kept local)", sha)
        return sha

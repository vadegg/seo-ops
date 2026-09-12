"""Thin git wrapper around the blog repo (clone, write, commit, push).

Uses an isolated SSH deploy key via GIT_SSH_COMMAND so it never touches
the operator's ssh-agent or global git config.
"""

from __future__ import annotations

import os
import subprocess
import shutil
import re
import hashlib
import shlex
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
        node_bin: str = "node",
    ):
        self._url = repo_url
        self._key = Path(deploy_key)
        self._branch = branch
        self._workdir = Path(workdir)
        self._log = logger
        self._node_bin = node_bin

    @property
    def repo_path(self) -> Path:
        return self._workdir

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if Path(self._node_bin).is_absolute():
            env["PATH"] = f"{Path(self._node_bin).parent}:{env.get('PATH', '')}"
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {shlex.quote(str(self._key))} -o IdentitiesOnly=yes "
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

    def assert_unique_slug(self, rel_path: str, slug: str, posts_dir: str) -> None:
        destination = self._workdir / rel_path
        for path in (self._workdir / posts_dir).glob("*.md"):
            if path == destination:
                continue
            match = re.search(r"^slug:\s*[\"']?([^\"'\n]+)",
                              path.read_text(encoding="utf-8"), re.MULTILINE)
            if match and match[1].strip() == slug:
                raise RuntimeError(f"slug '{slug}' already exists in {path.name}")

    def validate(self) -> None:
        """Run the actual site's build before a commit can reach autodeploy."""
        env = self._env()
        npm = shutil.which("npm", path=env.get("PATH"))
        if not npm:
            raise RuntimeError("npm is unavailable: configure NODE_BIN for the blog build")
        commands = []
        lock_hash = hashlib.sha256((self._workdir / "package-lock.json").read_bytes()).hexdigest()
        stamp = self._workdir / "node_modules" / ".seo-lock.sha256"
        installed_hash = stamp.read_text() if stamp.is_file() else ""
        reinstall = (installed_hash != lock_hash or
                     not (self._workdir / "node_modules" / ".bin" / "astro").exists())
        if reinstall:
            commands.append([npm, "ci", "--ignore-scripts", "--include=dev"])
        commands.append([npm, "run", "build"])
        for cmd in commands:
            result = subprocess.run(cmd, cwd=self._workdir, env=env,
                                    capture_output=True, text=True, timeout=300)
            if result.returncode:
                raise RuntimeError("blog validation failed: "
                                   + (result.stdout + result.stderr)[-5000:])
            if "ci" in cmd:
                stamp.write_text(lock_hash)
        if self._log:
            self._log.info("blog build and site checks passed")

    def commit_and_push(self, rel_paths: list[str], message: str,
                        *, push: bool = True) -> str:
        """Stage given paths, commit, optionally push. Returns commit sha.

        If there is nothing to commit (idempotent re-run), returns the
        existing HEAD sha without creating an empty commit.
        """
        for rp in rel_paths:
            self._git("add", rp)
        status = self._git("diff", "--cached", "--quiet", check=False)
        if status.returncode == 0:
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

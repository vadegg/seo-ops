"""Real local Git transport: tests cannot contact the production remote."""

import subprocess

import pytest

from clients.git_client import GitClient


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def test_dry_commit_then_real_push_and_unique_slug(tmp_path):
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-b", "main")
    (seed / "README.md").write_text("Initial content")
    git(seed, "add", ".")
    git(seed, "-c", "user.name=Test", "-c", "user.email=test@example.test",
        "commit", "-m", "Initial")
    subprocess.run(["git", "clone", "--bare", str(seed), str(origin)], check=True,
                   capture_output=True)
    initial = git(origin, "rev-parse", "main")
    client = GitClient(str(origin), tmp_path / "key with spaces", "main", tmp_path / "clone")
    client.ensure_clone()
    content = "---\nslug: article\n---\nContent"
    client.write_post("posts/2026-09-12-article.md", content)
    dry_sha = client.commit_and_push(["posts/2026-09-12-article.md"], "Article", push=False)
    assert dry_sha != initial
    assert git(origin, "rev-parse", "main") == initial
    client.ensure_clone()
    assert not (client.repo_path / "posts/2026-09-12-article.md").exists()
    client.write_post("posts/2026-09-12-article.md", content)
    sha = client.commit_and_push(["posts/2026-09-12-article.md"], "Article")
    assert git(origin, "rev-parse", "main") == sha
    with pytest.raises(RuntimeError, match="already exists"):
        client.assert_unique_slug("posts/2026-09-13-article.md", "article", "posts")
    # Build scripts may alter old metadata; unrelated edits must not create
    # an empty commit or get staged alongside an idempotent article.
    (client.repo_path / "README.md").write_text("Unstaged metadata change")
    assert client.commit_and_push(["posts/2026-09-12-article.md"], "Again") == sha
    assert git(origin, "show", "main:README.md") == "Initial content"


def test_validation_reinstalls_when_lockfile_changes(tmp_path, monkeypatch):
    client = GitClient("unused", tmp_path / "key", "main", tmp_path)
    astro = tmp_path / "node_modules/.bin/astro"
    astro.parent.mkdir(parents=True)
    astro.touch()
    lock = tmp_path / "package-lock.json"
    lock.write_text('{"version":1}')
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("clients.git_client.subprocess.run", run)
    monkeypatch.setattr("clients.git_client.shutil.which", lambda *a, **kw: "/fake/npm")
    client.validate()
    client.validate()
    lock.write_text('{"version":2}')
    client.validate()
    assert sum("ci" in cmd for cmd in calls) == 2
    assert sum("build" in cmd for cmd in calls) == 3

"""Failures reproduced in the September audit, with isolated external clients."""

import json
import logging
import subprocess
from types import SimpleNamespace

import pytest

from agents import editor, humanizer
from agents.runner import CLIAgentRunner
from agents.validation import EDITOR_CHECKS, ValidationError, validate_editor, validate_strategist
from clients.dataforseo import DataForSEOClient
from clients.deployment import DeploymentClient, check_article
from clients.evidence import EvidenceClient
from clients.retry import ClientError, PermanentClientError
from pipeline import artifacts as A
from pipeline.artifacts import ArtifactStore
from pipeline.escalation import STAGES
from pipeline.locking import AlreadyRunning, run_lock
from pipeline.orchestrator import run_pipeline, run_selected_steps
from pipeline.steps import gather_research_context
from tests.conftest import FakeDFS, FakeGit, FakeRunner


DAY = "2026-05-19"


def test_dry_run_then_real_publishes_without_regenerating(project, deps_factory):
    assert run_pipeline(project, run_date=DAY, dry_run=True, deps=deps_factory()) == 0
    deps = deps_factory()
    assert run_pipeline(project, run_date=DAY, deps=deps) == 0
    assert deps.git.pushed
    assert deps.agent_runner.calls == []
    status = ArtifactStore(project.runs_dir / DAY).read_json(A.PUBLISHER)
    assert status["status"] == "published"
    assert status["deployment"]["verified"]
    history = json.loads((project.backlog_dir / "topic_history.json").read_text())
    assert len(history["published"]) == 1


def test_build_failure_cannot_push_or_mutate_history(project, deps_factory):
    class BrokenBuild(FakeGit):
        def validate(self):
            raise RuntimeError("build rejected broken links")

    deps = deps_factory(git=BrokenBuild())
    assert run_pipeline(project, run_date=DAY, deps=deps) == 1
    assert not deps.git.pushed
    assert not ArtifactStore(project.runs_dir / DAY).exists(A.PUBLISHER)
    assert json.loads((project.backlog_dir / "topic_history.json").read_text())["published"] == []


def test_pending_deployment_resumes_without_agents_push_or_duplicate_state(project, deps_factory):
    class NotLive:
        def wait_for_post(self, *args):
            raise ClientError("deployment failed")

    deps = deps_factory(deployment=NotLive())
    assert run_pipeline(project, run_date=DAY, deps=deps) == 1
    store = ArtifactStore(project.runs_dir / DAY)
    assert deps.git.pushed
    assert store.read_json(A.PUBLISHER)["status"] == "pushed"

    class NoGit(FakeGit):
        def ensure_clone(self):
            pytest.fail("pending deployment must not reset or push git")

    retry = deps_factory(git=NoGit())
    assert run_pipeline(project, run_date=DAY, deps=retry) == 0
    assert retry.agent_runner.calls == []
    assert not retry.git.pushed
    assert store.read_json(A.PUBLISHER)["status"] == "published"
    assert len(json.loads((project.backlog_dir / "topic_history.json").read_text())["published"]) == 1
    links = json.loads((project.themes_dir / "internal_links.json").read_text())["posts"]
    assert sum(p["slug"] == "usability-testing-sample-size" for p in links) == 1


def test_failed_quality_cannot_be_bypassed_by_resume(project, deps_factory):
    for _ in range(2):
        deps = deps_factory(runner=FakeRunner(strategist_score=0.1))
        assert run_pipeline(project, run_date=DAY, dry_run=True, deps=deps) == 1
        assert not deps.git.pushed
    assert not ArtifactStore(project.runs_dir / DAY).exists(A.ASSEMBLER)


def test_force_upstream_invalidates_every_dependent_artifact(project, deps_factory):
    assert run_pipeline(project, run_date=DAY, dry_run=True, deps=deps_factory()) == 0
    assert run_selected_steps(project, run_date=DAY, step_names=["outliner"],
                              dry_run=True, force=True, deps=deps_factory()) == 0
    store = ArtifactStore(project.runs_dir / DAY)
    assert store.exists(A.OUTLINER)
    for artifact in (A.EVIDENCE, A.WRITER, A.EDITOR_MD, A.EDITOR_CRITIQUE,
                     A.HUMANIZER, A.UNIQUENESS, A.ASSEMBLER, A.PUBLISHER):
        assert not store.exists(artifact), artifact


def test_dfs_failure_keeps_gsc_and_is_attempted_once_per_run(project, deps_factory):
    class BrokenDFS(FakeDFS):
        calls = 0

        def keyword_metrics(self, keywords):
            self.calls += 1
            raise PermanentClientError("billing")

    dfs = BrokenDFS()
    deps = deps_factory(dfs=dfs)
    cache = {}
    for stage in (1, 2, 3, 4):
        rows, metrics, failed = gather_research_context(project, deps, STAGES[stage], None, cache)
        assert rows[0]["query"] == "usability testing sample size"
        assert not failed
    assert dfs.calls == 1


@pytest.mark.parametrize("task_error", [False, True])
def test_dfs_billing_failure_has_no_backoff_and_no_second_request(monkeypatch, task_error):
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(status_code=200 if task_error else 402,
                               raise_for_status=lambda: None,
                               json=lambda: {"status_code": 20000, "tasks": [
                                   {"status_code": 40200, "status_message": "Payment Required"}]})

    monkeypatch.setattr("requests.post", post)
    monkeypatch.setattr("clients.retry.time.sleep", lambda _: pytest.fail("billing must not retry"))
    client = DataForSEOClient("login", "password")
    for _ in range(2):
        with pytest.raises(PermanentClientError):
            client.keyword_metrics(["usability"])
    assert len(calls) == 1


def test_cli_preserves_jsonl_error_even_on_nonzero_exit(monkeypatch):
    event = json.dumps({"type": "turn.failed", "error": {"message": "specific backend failure"}})
    monkeypatch.setattr("agents.runner.subprocess.run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 1, stdout=event, stderr=""))
    with pytest.raises(ClientError, match="specific backend failure"):
        CLIAgentRunner(attempts=1).run(name="researcher", system="", user="",
                                     model="fake", tools=[], max_tokens=1, logger=None)


def test_cli_retries_transient_failures_but_not_authentication(monkeypatch):
    replies = [subprocess.CompletedProcess([], 1, stdout="", stderr="temporarily unavailable"),
               subprocess.CompletedProcess([], 0, stderr="", stdout='\n'.join([
                   json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}),
                   json.dumps({"type": "turn.completed", "usage": {}})]))]
    calls = []

    def run(*a, **kw):
        calls.append(1)
        return replies.pop(0)

    monkeypatch.setattr("agents.runner.subprocess.run", run)
    monkeypatch.setattr("clients.retry.time.sleep", lambda _: None)
    kwargs = dict(name="writer", system="", user="", model="fake", tools=[], max_tokens=1, logger=None)
    assert CLIAgentRunner().run(**kwargs) == "ok"
    assert len(calls) == 2
    replies.append(subprocess.CompletedProcess([], 1, stdout="", stderr="authentication required"))
    with pytest.raises(PermanentClientError):
        CLIAgentRunner().run(**kwargs)
    assert len(calls) == 3


def test_evidence_cannot_match_only_vs_or_numbers(tmp_path):
    (tmp_path / "interview.md").write_text("Другой разговор о работе vs учебе в 2026 году. " * 20)
    assert EvidenceClient(tmp_path).search("SUS vs UMUX-Lite vs SUPR-Q 2026 usability questionnaire") == []


def test_evidence_handles_unicode_and_keeps_real_matches(tmp_path):
    (tmp_path / "notes.md").write_text("Проверяем понятность навигации: названия разделов и поиск нужного маршрута. " * 5)
    hits = EvidenceClient(tmp_path).search("понятность навигации")
    assert hits and hits[0]["matched_terms"] == ["навигации", "понятность"]


@pytest.mark.parametrize("checklist", [{"seo": True}, {k: "true" for k in EDITOR_CHECKS}, {}])
def test_incomplete_or_string_editor_checks_are_invalid(checklist):
    with pytest.raises(ValidationError):
        validate_editor({"edited_markdown": "Text", "critique": {"checklist": checklist, "passed": True}})


@pytest.mark.parametrize("score", [float('nan'), float('inf'), -0.1, 1.1, True])
def test_topic_score_must_be_finite_and_in_range(score):
    with pytest.raises(ValidationError):
        validate_strategist({"topic": "Topic", "primary_keyword": "Keyword", "score": score})


@pytest.mark.parametrize("replacement", [
    "## Heading\n\nTry 9 users. [Guide](/guide)",
    "## Heading\n\nTry 5 users.",
    "## Other heading\n\nTry 5 users. [Guide](/guide)",
])
def test_humanizer_cannot_change_numbers_links_or_headings(replacement):
    original = "## Heading\n\nTry 5 users. [Guide](/guide)"
    runner = SimpleNamespace(run=lambda **kw: replacement)
    assert humanizer.run(runner, model="m", tools=[], max_tokens=1, logger=None,
                         draft_md=original, style_guide="", evidence_passages=[]) == original


def test_editor_receives_the_complete_long_draft():
    draft = "## Heading\n\n" + "Detailed evidence. " * 1200 + "END-OF-DRAFT"
    captured = []

    def run(**kw):
        captured.append(kw['user'])
        return json.dumps({"edited_markdown": draft, "critique": {
            "checklist": {k: True for k in EDITOR_CHECKS}, "passed": True}})

    editor.run(SimpleNamespace(run=run), model="m", tools=[], max_tokens=1,
               logger=None, draft_md=draft, brief={}, style_guide="",
               evidence_passages=[], iteration=1)
    assert "END-OF-DRAFT" in captured[0]


def response(status=200, *, title="Title", canonical="https://example.com/post/", noindex=False):
    return SimpleNamespace(status_code=status, url="https://example.com/post/", headers={},
        text=f'<link rel="canonical" href="{canonical}"><h1>{title}</h1>'
        + ('<meta name="robots" content="noindex">' if noindex else ''))


@pytest.mark.parametrize("bad", [response(404), response(title="Home"), response(noindex=True), response(canonical="https://example.com/")])
def test_deployment_rejects_error_pages_wrong_articles_and_noindex(bad):
    assert check_article(bad, "https://example.com/post/", "Title")


def test_deployment_waits_until_the_expected_article_is_live():
    replies = [response(404), response()]
    now = [0]
    client = DeploymentClient(timeout=30, poll_interval=5, get=lambda *a, **kw: replies.pop(0),
                              monotonic=lambda: now[0], sleep=lambda delay: now.__setitem__(0, now[0] + delay))
    assert client.wait_for_post("https://example.com/post/", "Title")["verified"]
    assert now[0] == 5


def test_deployment_timeout_leaves_a_recoverable_failure():
    client = DeploymentClient(timeout=0, get=lambda *a, **kw: response(404))
    with pytest.raises(ClientError, match="pushed but deployment not verified"):
        client.wait_for_post("https://example.com/post/", "Title")


def test_two_runs_cannot_use_the_shared_checkout(tmp_path):
    with run_lock(tmp_path):
        with pytest.raises(AlreadyRunning):
            with run_lock(tmp_path):
                pytest.fail("second writer entered")
    with run_lock(tmp_path):
        pass


def test_old_humanizer_artifact_without_final_approval_is_not_publishable(project, deps_factory):
    assert run_pipeline(project, run_date=DAY, dry_run=True, deps=deps_factory()) == 0
    store = ArtifactStore(project.runs_dir / DAY)
    store.write_text(A.HUMANIZER, store.read_text(A.EDITOR_MD) + "\nAn unsupported assertion.\n")
    store.path("05c-humanizer.critique.json").unlink(missing_ok=True)
    store.path(A.ASSEMBLER).unlink()
    assert run_selected_steps(project, run_date=DAY, step_names=["assembler"],
                              dry_run=True, deps=deps_factory()) == 1
    deps = deps_factory()
    assert run_pipeline(project, run_date=DAY, dry_run=True, deps=deps) == 0
    assert "humanizer" in deps.agent_runner.calls


def test_pending_deployment_rejects_changed_assembled_content(project, deps_factory):
    class NotLive:
        def wait_for_post(self, *args):
            raise ClientError("not yet live")

    assert run_pipeline(project, run_date=DAY, deps=deps_factory(deployment=NotLive())) == 1
    store = ArtifactStore(project.runs_dir / DAY)
    store.write_text(A.ASSEMBLER, store.read_text(A.ASSEMBLER) + "Changed after push")
    deps = deps_factory()
    assert run_pipeline(project, run_date=DAY, deps=deps) == 1
    assert not deps.git.pushed and deps.agent_runner.calls == []
    assert store.read_json(A.PUBLISHER)["status"] == "pushed"


def test_dfs_cost_limit_is_permanent_but_search_engine_failure_retries(monkeypatch):
    monkeypatch.setattr("clients.retry.time.sleep", lambda _: None)
    calls = []
    codes = [40101, 20000, 40203]

    def post(*args, **kwargs):
        calls.append(1)
        code = codes.pop(0)
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
            json=lambda: {"status_code": 20000, "tasks": [
                {"status_code": code, "result": []}]})

    monkeypatch.setattr("requests.post", post)
    client = DataForSEOClient("login", "password")
    assert client.keyword_metrics(["keyword"]) == []
    assert len(calls) == 2
    with pytest.raises(PermanentClientError, match="40203"):
        client.keyword_metrics(["keyword"])
    assert len(calls) == 3

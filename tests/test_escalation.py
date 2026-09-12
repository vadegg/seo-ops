from pipeline import artifacts as A
from pipeline.artifacts import ArtifactStore
from pipeline.orchestrator import run_pipeline
from tests.conftest import FailingEditorRunner, FakeDFS, FakeGSC, FakeRunner


def _esc_log(project, date="2026-05-19"):
    return (project.runs_dir / date / "escalation.log").read_text()


def test_low_score_exhausts_stages_without_publishing(project, deps_factory):
    deps = deps_factory(runner=FakeRunner(strategist_score=0.1))
    rc = run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    assert rc == 1

    log = _esc_log(project)
    assert "stage 4" in log  # walked the ladder to the guarantee
    assert not ArtifactStore(project.runs_dir / "2026-05-19").exists(A.PUBLISHER)
    assert not deps.git.pushed
    # degradations surfaced in the run report
    assert any("degraded to escalation level" in r["detailed"]
               for r in deps.fleet.reports)


def test_api_unavailable_falls_through_to_independent_stage(project,
                                                            deps_factory):
    deps = deps_factory(runner=FakeRunner(strategist_score=0.9),
                        gsc=FakeGSC(fail=True), dfs=FakeDFS(fail=True))
    rc = run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    assert rc == 0

    status = ArtifactStore(project.runs_dir / "2026-05-19").read_json(
        A.PUBLISHER)
    # GSC+DFS dead at stages 1–2 -> reach stage 3 (web-search, no paid API)
    assert status["escalation_stage"] >= 3
    assert "stage 3" in _esc_log(project)


def test_editor_failed_final_pass_blocks_publication(project, deps_factory):
    deps = deps_factory(runner=FailingEditorRunner(strategist_score=0.9))
    rc = run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    assert rc == 1

    s = ArtifactStore(project.runs_dir / "2026-05-19")
    crit = s.read_json(A.EDITOR_CRITIQUE)
    assert crit.get("forced_final") is True
    # forced-final logs an ERROR degradation — surfaced in the report metrics.
    # It is NOT a crash, so it never pings Telegram.
    report = deps.fleet.reports[0]
    assert report["status"] == "fail"
    assert report["metrics"]["errors"] >= 1
    assert deps.telegram.messages == []
    # still published despite failing checklist (day never skipped)
    assert not s.exists(A.PUBLISHER)


def test_duplicate_topic_is_rejected_at_every_stage(
        project, deps_factory):
    """A topic that repeats a published post must not be accepted quietly:
    it walks the ladder (a fresh Researcher/Strategist pass may find
    something else) and, if nothing better turns up, still publishes — but
    loudly. This is the guard that was missing while one topic shipped eight
    times between 03.08 and 21.08.2026."""
    import json

    (project.backlog_dir / "topic_history.json").write_text(json.dumps(
        {"published": [
            {"topic": "How many users for usability testing",
             "keyword": "usability testing sample size",
             "slug": "usability-testing-sample-size",
             "url": "https://blog.glasgow.works/blog/usability-testing-sample-size",
             "date": "2026-05-10"}]}), encoding="utf-8")
    deps = deps_factory(runner=FakeRunner(strategist_score=0.9))
    rc = run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    assert rc == 1

    log = _esc_log(project)
    assert "duplicate" in log.lower(), log
    assert "stage 4" in log            # walked the ladder looking for another topic
    # the publish guarantee still holds
    assert not ArtifactStore(project.runs_dir / "2026-05-19").exists(A.PUBLISHER)
    # and the run report says why the day is degraded
    assert any("duplicate" in r["detailed"].lower() for r in deps.fleet.reports)

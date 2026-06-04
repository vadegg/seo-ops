from pipeline import artifacts as A
from pipeline.artifacts import ArtifactStore
from pipeline.orchestrator import run_pipeline
from tests.conftest import FailingEditorRunner, FakeDFS, FakeGSC, FakeRunner


def _esc_log(project, date="2026-05-19"):
    return (project.runs_dir / date / "escalation.log").read_text()


def test_low_score_escalates_to_guarantee_and_publishes(project, deps_factory):
    deps = deps_factory(runner=FakeRunner(strategist_score=0.1))
    rc = run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    assert rc == 0

    log = _esc_log(project)
    assert "stage 4" in log  # walked the ladder to the guarantee
    status = ArtifactStore(project.runs_dir / "2026-05-19").read_json(
        A.PUBLISHER)
    assert status["escalation_stage"] == 4
    # degraded alerts fired
    assert any("degraded to escalation level" in t
               for _, t in deps.telegram.messages)


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


def test_editor_forced_final_opus_and_hard_alert(project, deps_factory):
    deps = deps_factory(runner=FailingEditorRunner(strategist_score=0.9))
    rc = run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    assert rc == 0

    s = ArtifactStore(project.runs_dir / "2026-05-19")
    crit = s.read_json(A.EDITOR_CRITIQUE)
    assert crit.get("forced_final") is True
    assert any(level == "hard" for level, _ in deps.telegram.messages)
    # still published despite failing checklist (day never skipped)
    assert s.exists(A.PUBLISHER)

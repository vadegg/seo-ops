"""Regression tests for code-review fixes (#1 editor resilience,
#2 isolated-run alerts, #4 escalation-ceiling WARN)."""

import dataclasses

from pipeline import artifacts as A
from pipeline.artifacts import ArtifactStore
from pipeline.orchestrator import run_pipeline, run_selected_steps
from pipeline.steps import STEP_NAMES
from tests.conftest import FakeRunner


class EditorGarbageRunner(FakeRunner):
    """Editor returns unparseable output every time -> run_json raises
    ClientError on both attempts, at every iteration AND the forced final."""

    def _canned(self, *, name, model):
        if name == "editor":
            return "not json — the model melted down"
        return super()._canned(name=name, model=model)


# ---- #1 editor never abandons the day -------------------------------------
def test_editor_clienterror_still_publishes(project, deps_factory):
    deps = deps_factory(runner=EditorGarbageRunner(strategist_score=0.9))
    rc = run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    assert rc == 0                                   # day not abandoned
    s = ArtifactStore(project.runs_dir / "2026-05-19")
    assert s.exists(A.EDITOR_MD)                     # best draft shipped
    crit = s.read_json(A.EDITOR_CRITIQUE)
    assert crit.get("forced_final") is True
    # the editor body fell back to the writer's draft
    assert s.read_text(A.EDITOR_MD) == s.read_text(A.WRITER)


# ---- #2 isolated/resume runs still alert ----------------------------------
def test_selected_steps_send_digest(project, deps_factory):
    deps = deps_factory()
    run_selected_steps(project, run_date="2026-05-19", step_names=STEP_NAMES,
                       dry_run=True, deps=deps)
    assert len(deps.telegram.messages) == 1          # finalize ran


# ---- #4 ceiling acceptance is a degradation, not a clean run --------------
def test_escalation_ceiling_emits_warning(project, deps_factory):
    cfg = dataclasses.replace(project, max_stage=1)
    deps = deps_factory(runner=FakeRunner(strategist_score=0.1))
    run_pipeline(cfg, run_date="2026-05-19", dry_run=True, deps=deps)
    log = (cfg.runs_dir / "2026-05-19" / "run.log").read_text()
    assert "ceiling reached" in log
    level, _ = deps.telegram.messages[0]
    assert level != "info"                           # forced low-score day

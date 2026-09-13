"""Regression tests for code-review fixes (#1 editor resilience,
#2 isolated-run reports, #4 escalation-ceiling WARN)."""

import dataclasses
import json

import pytest

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


@pytest.mark.parametrize("always_invalid", [False, True])
def test_editor_frontmatter_is_retried_before_assembly(project, deps_factory,
                                                       always_invalid):
    class FrontmatterRunner(FakeRunner):
        editor_attempts = 0

        def _canned(self, *, name, model):
            reply = super()._canned(name=name, model=model)
            if name == "editor":
                self.editor_attempts += 1
                if always_invalid or self.editor_attempts == 1:
                    data = json.loads(reply)
                    data["edited_markdown"] = (
                        '---\ntitle: "Unexpected metadata"\n---\n\n'
                        + data["edited_markdown"])
                    return json.dumps(data)
            return reply

    runner = FrontmatterRunner()
    deps = deps_factory(runner=runner)
    rc = run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    store = ArtifactStore(project.runs_dir / "2026-05-19")
    assert not deps.git.pushed
    if always_invalid:
        assert rc == 1
        assert runner.editor_attempts == 6
        assert not store.exists(A.EDITOR_MD)
        assert not store.exists(A.ASSEMBLER)
        assert not store.exists(A.PUBLISHER)
        assert store.exists("05-editor.rejected.md")
    else:
        assert rc == 0
        assert runner.editor_attempts >= 2
        assert not store.read_text(A.EDITOR_MD).startswith("---")
        assert store.exists(A.ASSEMBLER)
    log = (project.runs_dir / "2026-05-19" / "run.log").read_text()
    assert "frontmatter" in log
    assert "re-prompting once" in log


# ---- #1 editor never abandons the day -------------------------------------
def test_editor_clienterror_preserves_draft_but_never_publishes(project, deps_factory):
    deps = deps_factory(runner=EditorGarbageRunner(strategist_score=0.9))
    rc = run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    assert rc == 1
    s = ArtifactStore(project.runs_dir / "2026-05-19")
    assert not s.exists(A.EDITOR_MD)
    assert not s.exists(A.PUBLISHER)
    crit = s.read_json(A.EDITOR_CRITIQUE)
    assert crit.get("forced_final") is True
    # the editor body fell back to the writer's draft
    assert s.read_text("05-editor.rejected.md") == s.read_text(A.WRITER)


# ---- #2 isolated/resume runs write an internal report (no fleet submit) ----
def test_selected_steps_writes_report(project, deps_factory):
    deps = deps_factory()
    run_selected_steps(project, run_date="2026-05-19", step_names=STEP_NAMES,
                       dry_run=True, deps=deps)
    report = json.loads(
        (project.runs_dir / "2026-05-19" / "report.json").read_text())
    assert report["trigger"] == "manual"             # finalize ran
    assert deps.fleet.reports == []                  # manual run never ships


# ---- #4 ceiling acceptance is a degradation, not a clean run --------------
def test_escalation_ceiling_emits_warning(project, deps_factory):
    cfg = dataclasses.replace(project, max_stage=1)
    deps = deps_factory(runner=FakeRunner(strategist_score=0.1))
    run_pipeline(cfg, run_date="2026-05-19", dry_run=True, deps=deps)
    log = (cfg.runs_dir / "2026-05-19" / "run.log").read_text()
    assert "ceiling reached" in log
    assert deps.fleet.reports[0]["metrics"]["degradations"] > 0  # forced day

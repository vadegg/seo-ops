"""#39 Humanizer step wiring: sits between editor and assembler, resumes,
is non-blocking, and feeds the assembler its output."""

from __future__ import annotations

from pipeline import artifacts as A
from pipeline.artifacts import ArtifactStore
from pipeline.orchestrator import run_selected_steps
from pipeline.steps import STEP_NAMES


def _store(project, date="2026-05-19"):
    return ArtifactStore(project.runs_dir / date)


def test_humanizer_in_registry_between_editor_and_assembler():
    assert "humanizer" in STEP_NAMES
    assert STEP_NAMES.index("editor") < STEP_NAMES.index("humanizer") \
        < STEP_NAMES.index("assembler")


def test_humanizer_writes_artifact_and_feeds_assembler(project, deps_factory):
    rc = run_selected_steps(project, run_date="2026-05-19",
                            step_names=STEP_NAMES, dry_run=True,
                            deps=deps_factory())
    assert rc == 0
    s = _store(project)
    assert s.exists(A.HUMANIZER)
    # assembled post is built from a non-empty body
    assert s.read_text(A.ASSEMBLER).strip()


def test_humanizer_resumes_when_artifact_present(project, deps_factory):
    # seed everything up to editor
    run_selected_steps(project, run_date="2026-05-19",
                       step_names=["researcher", "strategist", "outliner",
                                   "writer", "editor", "humanizer"],
                       dry_run=True, deps=deps_factory())
    assert _store(project).exists(A.HUMANIZER)
    # re-run humanizer alone: output present -> skip, no agent call
    deps = deps_factory()
    run_selected_steps(project, run_date="2026-05-19",
                       step_names=["humanizer"], dry_run=True, deps=deps)
    assert "humanizer" not in deps.agent_runner.calls

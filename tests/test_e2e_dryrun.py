import json

from pipeline import artifacts as A
from pipeline.artifacts import ArtifactStore
from pipeline.orchestrator import run_pipeline


def test_full_pipeline_dry_run(project, deps_factory):
    deps = deps_factory()
    rc = run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    assert rc == 0

    s = ArtifactStore(project.runs_dir / "2026-05-19")
    for name in (A.RESEARCHER, A.STRATEGIST, A.OUTLINER, A.WRITER,
                 A.EDITOR_MD, A.ASSEMBLER, A.PUBLISHER):
        assert s.exists(name), f"missing artifact {name}"

    post = s.read_text(A.ASSEMBLER)
    assert post.startswith("---\n")
    assert "application/ld+json" in post

    status = s.read_json(A.PUBLISHER)
    assert status["status"] == "dry_run"
    assert deps.git.pushed is False

    # dry-run must not mutate persistent stores
    th = json.loads((project.backlog_dir / "topic_history.json").read_text())
    assert th["published"] == []

    # run.log written
    assert (project.runs_dir / "2026-05-19" / "run.log").is_file()


def test_resume_skips_completed_steps(project, deps_factory):
    deps = deps_factory()
    run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    first_calls = list(deps.agent_runner.calls)
    assert first_calls

    # Second run: every artifact exists -> no agent should be called again.
    deps2 = deps_factory()
    run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps2)
    assert deps2.agent_runner.calls == []

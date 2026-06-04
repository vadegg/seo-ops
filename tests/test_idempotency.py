from pipeline import artifacts as A
from pipeline.artifacts import ArtifactStore
from pipeline.orchestrator import run_pipeline


class ExplodingRunner:
    def run(self, **kw):
        raise AssertionError("agent must not run when already published")


def test_published_run_is_noop(project, deps_factory):
    run_dir = project.runs_dir / "2026-05-19"
    ArtifactStore(run_dir).write_json(A.PUBLISHER, {"status": "published"})

    deps = deps_factory(runner=ExplodingRunner())
    rc = run_pipeline(project, run_date="2026-05-19", dry_run=False,
                      deps=deps)
    assert rc == 0
    # topic_history untouched (no second publish)
    import json
    th = json.loads((project.backlog_dir / "topic_history.json")
                     .read_text())
    assert th["published"] == []

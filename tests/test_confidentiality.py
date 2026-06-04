import json

from pipeline import artifacts as A
from pipeline.artifacts import ArtifactStore
from pipeline.orchestrator import run_pipeline
from tests.conftest import FakeRunner


class LeakyRunner(FakeRunner):
    """Editor lets a CONFIDENTIAL marker through (agent missed it).
    The deterministic assembler scrub must catch it."""

    def run(self, *, name, **kw):
        if name == "editor":
            self.calls.append(name)
            return json.dumps({
                "edited_markdown": (
                    "## Findings\n\n"
                    "General insight about task coverage.\n\n"
                    "CONFIDENTIAL: Acme Corp churned 18% in Q3 per their NDA "
                    "report.\n\n"
                    "More general guidance follows.\n"),
                "critique": {"checklist": {
                    "on_brief": True, "style_guide": True, "seo": True,
                    "internal_links": True, "evidence_grounded": True,
                    "no_client_leak": True}, "passed": True,
                    "notes": "missed the leak"}})
        return super().run(name=name, **kw)


def test_confidential_marker_never_reaches_post(project, deps_factory):
    deps = deps_factory(runner=LeakyRunner(strategist_score=0.9))
    rc = run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    assert rc == 0

    s = ArtifactStore(project.runs_dir / "2026-05-19")
    post = s.read_text(A.ASSEMBLER)
    assert "CONFIDENTIAL" not in post.upper()
    assert "Acme Corp" not in post
    assert "NDA" not in post.upper()
    # general content survived
    assert "task coverage" in post

    # leak detected -> hard alert + escalation log entry
    assert any(level == "hard" for level, _ in deps.telegram.messages)
    esc = (project.runs_dir / "2026-05-19" / "escalation.log").read_text()
    assert "confidentiality" in esc.lower()

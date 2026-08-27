"""Fleet reporting: FleetClient submission (fail-soft, env, stdin), the
ReportInput builder shape, and the orchestrator crash / skipped paths."""

import json
from types import SimpleNamespace

from clients.fleet import FleetClient
from pipeline import artifacts as A
from pipeline.artifacts import ArtifactStore
from pipeline.orchestrator import run_pipeline
from pipeline.steps import build_run_report
from tests.conftest import FakeRunner

REPORT = {
    "animal": "nightingale-seo-autoblog", "run_id": "rid", "trigger": "cron",
    "trigger_id": "cron:2026-05-19", "attempt": 1,
    "started_at": "2026-05-19T01:00:00+00:00",
    "finished_at": "2026-05-19T01:05:00+00:00", "status": "ok", "error": None,
    "detailed": "d", "artifacts": [], "metrics": {"x": 1}}


class RecordingRunner:
    """Stand-in for subprocess.run — records calls, never spawns node."""

    def __init__(self, rc=0, stdout="reports/zoo/2026-05-19/x.json\n",
                 stderr="", raises=None):
        self.rc, self.stdout, self.stderr = rc, stdout, stderr
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, cmd, *, input, env, **kw):
        self.calls.append({"cmd": cmd, "input": input, "env": env})
        if self.raises:
            raise self.raises
        return SimpleNamespace(returncode=self.rc, stdout=self.stdout,
                               stderr=self.stderr)


# ---- FleetClient.submit ----------------------------------------------------
def test_submit_pipes_json_and_env():
    rec = RecordingRunner()
    fc = FleetClient("/ark", "zoo", no_sync=True, node_bin="node", runner=rec)
    out = fc.submit(REPORT)
    assert out == "reports/zoo/2026-05-19/x.json"
    call = rec.calls[0]
    assert call["cmd"][0] == "node"
    assert call["cmd"][1].endswith("ark/river-report-flow/publish-cli.ts")
    assert call["env"]["ARK_ZOO"] == "zoo"
    assert call["env"]["ARK_NO_SYNC"] == "1"
    assert json.loads(call["input"]) == REPORT      # exact payload on stdin


def test_no_sync_env_absent_when_false():
    rec = RecordingRunner()
    FleetClient("/ark", "zoo", no_sync=False, runner=rec).submit(REPORT)
    assert "ARK_NO_SYNC" not in rec.calls[0]["env"]


def test_dry_run_skips_submission():
    rec = RecordingRunner()
    assert FleetClient("/ark", runner=rec).submit(REPORT, dry_run=True) is None
    assert rec.calls == []


def test_empty_ark_repo_disables():
    rec = RecordingRunner()
    assert FleetClient("", runner=rec).submit(REPORT) is None
    assert rec.calls == []


def test_missing_node_is_fail_soft():
    rec = RecordingRunner(raises=FileNotFoundError("node"))
    assert FleetClient("/ark", runner=rec).submit(REPORT) is None   # no raise


def test_nonzero_exit_is_fail_soft():
    rec = RecordingRunner(rc=1, stderr="ajv: invalid")
    assert FleetClient("/ark", runner=rec).submit(REPORT) is None


# ---- build_run_report shape (fleet ReportInput contract) -------------------
def test_build_run_report_shape(project):
    run_dir = project.runs_dir / "2026-05-19"
    store = ArtifactStore(run_dir)
    store.write_json(A.PUBLISHER, {
        "status": "published", "slug": "s", "url": "https://x/blog/s",
        "file": "src/content/blog/2026-05-19-s.md", "escalation_stage": 2,
        "backlog": {"added": 1, "pruned": 0, "kept": 5}})
    usage = {"total": {"input_tokens": 10, "output_tokens": 20, "usd": 0.5}}
    rep = build_run_report(
        project, run_dir, "2026-05-19", SimpleNamespace(degradations=[]), usage,
        status="ok", started_at="2026-05-19T01:00:00+00:00",
        finished_at="2026-05-19T01:05:00+00:00", run_id="rid",
        trigger="cron", trigger_id="cron:2026-05-19")

    assert set(rep) == {
        "animal", "run_id", "trigger", "trigger_id", "attempt", "started_at",
        "finished_at", "status", "error", "detailed", "artifacts",
        "metrics"}
    assert "brief" not in rep  # schema v5: only shepherd writes short summaries
    # module fills these — we must NOT send them
    for k in ("schema_version", "zoo", "duration_ms"):
        assert k not in rep
    assert all(isinstance(a, str) for a in rep["artifacts"])
    assert all(isinstance(v, (int, float)) for v in rep["metrics"].values())
    assert rep["metrics"]["backlog_added"] == 1
    assert rep["metrics"]["escalation_stage"] == 2
    assert "run.log" in " ".join(rep["artifacts"])


# ---- orchestrator crash path -> status=fail + fatal Telegram ping ----------
class CrashingRunner(FakeRunner):
    def run(self, *, name, **kw):
        if name == "researcher":
            raise RuntimeError("boom in researcher")
        return super().run(name=name, **kw)


def test_crash_writes_fail_report_and_pings_telegram(project, deps_factory):
    deps = deps_factory(runner=CrashingRunner())
    rc = run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    assert rc == 1
    report = deps.fleet.reports[0]
    assert report["status"] == "fail"
    assert report["error"]                            # traceback captured
    assert any("run.log" in a for a in report["artifacts"])
    assert any(level == "hard" for level, _ in deps.telegram.messages)
    saved = json.loads(
        (project.runs_dir / "2026-05-19" / "report.json").read_text())
    assert saved["status"] == "fail"


# ---- idempotent no-op -> status=skipped ------------------------------------
def test_second_run_of_published_day_is_skipped(project, deps_factory):
    run_pipeline(project, run_date="2026-05-19", dry_run=False,
                 deps=deps_factory())
    deps2 = deps_factory()
    rc = run_pipeline(project, run_date="2026-05-19", dry_run=False, deps=deps2)
    assert rc == 0
    assert len(deps2.fleet.reports) == 1
    assert deps2.fleet.reports[0]["status"] == "skipped"

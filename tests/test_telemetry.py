"""Run telemetry: usage accounting (#5), degradation summary (#3),
empty-evidence signal (#4), single fleet run report (#8)."""

import json

from pipeline.orchestrator import run_pipeline
from pipeline.usage import summarize
from tests.conftest import FakeEvidence, FakeRunner

PRICES = (("m-opus", 15.0, 75.0), ("m-sonnet", 3.0, 15.0))


def _run_dir(project, date="2026-05-19"):
    return project.runs_dir / date


# ---- #5 usage --------------------------------------------------------------
def test_summarize_tokens_and_cost():
    records = [
        {"agent": "writer", "model": "m-sonnet",
         "input_tokens": 1_000_000, "output_tokens": 1_000_000},
        {"agent": "editor", "model": "m-opus",
         "input_tokens": 0, "output_tokens": 1_000_000},
    ]
    r = summarize(records, PRICES)
    assert r["total"]["input_tokens"] == 1_000_000
    assert r["total"]["output_tokens"] == 2_000_000
    # sonnet: 3 + 15 = 18 ; opus: 0 + 75 = 75 ; total 93
    assert abs(r["total"]["usd"] - 93.0) < 1e-6
    assert r["by_agent"]["writer"]["calls"] == 1


def test_unknown_model_listed_not_crash():
    r = summarize([{"agent": "x", "model": "mystery",
                    "input_tokens": 10, "output_tokens": 10}], PRICES)
    assert r["unknown_models"] == ["mystery"]
    assert r["total"]["usd"] == 0.0


def test_usage_json_written_after_run(project, deps_factory):
    deps = deps_factory()
    run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    usage = json.loads((_run_dir(project) / "usage.json").read_text())
    assert usage["total"]["output_tokens"] > 0
    assert "researcher" in usage["by_agent"]


# ---- #8 single fleet run report -------------------------------------------
def test_degraded_run_writes_one_report(project, deps_factory):
    # low score -> walks the escalation ladder (several degradations)
    deps = deps_factory(runner=FakeRunner(strategist_score=0.1))
    run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    assert len(deps.fleet.reports) == 1              # one consolidated report
    report = deps.fleet.reports[0]
    assert report["status"] == "ok"
    assert "degraded to escalation level" in report["detailed"]  # degradations
    assert report["metrics"]["output_tokens"] > 0    # article cost (#5)
    assert report["metrics"]["degradations"] > 0
    # persisted internally too (machine-readable run summary)
    saved = json.loads((_run_dir(project) / "report.json").read_text())
    assert saved["run_id"] == report["run_id"]


def test_clean_run_report_has_no_degradations(project, deps_factory):
    # Shrink the internal-link corpus below the floor so the (legitimate)
    # #13 under-link WARN does not fire — isolating the clean path.
    (project.themes_dir / "internal_links.json").write_text(
        json.dumps({"posts": []}), encoding="utf-8")
    deps = deps_factory()
    run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    assert len(deps.fleet.reports) == 1
    report = deps.fleet.reports[0]
    assert report["status"] == "ok"
    assert report["metrics"]["degradations"] == 0


# ---- #3 degradation summary in run.log ------------------------------------
def test_run_log_has_degradation_summary(project, deps_factory):
    deps = deps_factory(runner=FakeRunner(strategist_score=0.1))
    run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    log = (_run_dir(project) / "run.log").read_text()
    assert "=== degradations" in log
    assert "degraded to escalation level" in log


# ---- #4 empty-evidence signal ---------------------------------------------
def test_empty_evidence_warns(project, deps_factory):
    deps = deps_factory(evidence=FakeEvidence(passages=[]))
    run_pipeline(project, run_date="2026-05-19", dry_run=True, deps=deps)
    log = (_run_dir(project) / "run.log").read_text()
    assert "evidence empty" in log

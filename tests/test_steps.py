import argparse
import json

from pipeline import artifacts as A
from pipeline.artifacts import ArtifactStore
from pipeline.orchestrator import run_selected_steps
from pipeline.publisher import _reconcile_keyword_backlog
from run import _resolve_steps
from pipeline.steps import STEP_NAMES


def _store(project, date="2026-05-19"):
    return ArtifactStore(project.runs_dir / date)


def _esc_log(project, date="2026-05-19"):
    p = project.runs_dir / date / "escalation.log"
    return p.read_text() if p.is_file() else ""


def test_single_step_writes_only_its_artifact(project, deps_factory):
    deps = deps_factory()
    rc = run_selected_steps(project, run_date="2026-05-19",
                            step_names=["researcher"], dry_run=True, deps=deps)
    assert rc == 0
    s = _store(project)
    assert s.exists(A.RESEARCHER)
    assert not s.exists(A.STRATEGIST)
    assert deps.agent_runner.calls == ["researcher"]


def test_chaining_steps_separately(project, deps_factory):
    run_selected_steps(project, run_date="2026-05-19",
                       step_names=["researcher"], dry_run=True,
                       deps=deps_factory())
    deps = deps_factory()
    rc = run_selected_steps(project, run_date="2026-05-19",
                            step_names=["strategist"], dry_run=True, deps=deps)
    assert rc == 0
    assert _store(project).exists(A.STRATEGIST)
    assert deps.agent_runner.calls == ["strategist"]


def test_missing_input_errors_with_hint(project, deps_factory):
    deps = deps_factory()
    rc = run_selected_steps(project, run_date="2026-05-19",
                            step_names=["strategist"], dry_run=True, deps=deps)
    assert rc == 2  # StepInputError -> exit code 2
    assert not _store(project).exists(A.STRATEGIST)
    assert deps.agent_runner.calls == []


def test_from_step_resumes_to_publisher(project, deps_factory):
    # seed steps 1–2
    run_selected_steps(project, run_date="2026-05-19",
                       step_names=["researcher", "strategist"], dry_run=True,
                       deps=deps_factory())
    deps = deps_factory()
    rc = run_selected_steps(
        project, run_date="2026-05-19",
        step_names=["outliner", "writer", "editor", "assembler", "publisher"],
        dry_run=True, deps=deps)
    assert rc == 0
    s = _store(project)
    for name in (A.OUTLINER, A.WRITER, A.EDITOR_MD, A.ASSEMBLER, A.PUBLISHER):
        assert s.exists(name), f"missing {name}"
    # 1–2 were not re-run
    assert "researcher" not in deps.agent_runner.calls
    assert "strategist" not in deps.agent_runner.calls


def test_resume_skips_and_force_reruns(project, deps_factory):
    run_selected_steps(project, run_date="2026-05-19",
                       step_names=["researcher"], dry_run=True,
                       deps=deps_factory())

    # without --force: output present -> skip, no agent call
    skip = deps_factory()
    run_selected_steps(project, run_date="2026-05-19",
                       step_names=["researcher"], dry_run=True, deps=skip)
    assert skip.agent_runner.calls == []

    # with --force: re-run
    forced = deps_factory()
    run_selected_steps(project, run_date="2026-05-19",
                       step_names=["researcher"], dry_run=True, force=True,
                       deps=forced)
    assert forced.agent_runner.calls == ["researcher"]


def test_isolated_researcher_does_not_escalate(project, deps_factory):
    deps = deps_factory()
    run_selected_steps(project, run_date="2026-05-19",
                       step_names=["researcher"], dry_run=True,
                       start_stage=3, deps=deps)
    assert _store(project).exists(A.RESEARCHER)
    assert "degraded to escalation level" not in _esc_log(project)


def test_real_publish_folds_candidates_into_backlog(project, deps_factory):
    deps = deps_factory()
    rc = run_selected_steps(
        project, run_date="2026-05-19",
        step_names=STEP_NAMES, dry_run=False, deps=deps)
    assert rc == 0
    backlog = json.loads(
        (project.backlog_dir / "keyword_backlog.json").read_text())
    by_kw = {c["keyword"]: c for c in backlog["candidates"]}
    # non-selected, above-floor candidate persisted with score + date
    assert by_kw["remote moderated usability testing"]["score"] == 0.6
    assert by_kw["remote moderated usability testing"]["date"] == "2026-05-19"
    # surplus persisted too
    assert "card sorting guide" in by_kw
    # below-floor candidate pruned
    assert "ux survey tools 2020" not in by_kw
    # the published keyword is not parked back in the reserve
    assert "usability testing sample size" not in by_kw


def test_dry_run_leaves_backlog_untouched(project, deps_factory):
    before = (project.backlog_dir / "keyword_backlog.json").read_text()
    rc = run_selected_steps(
        project, run_date="2026-05-19",
        step_names=STEP_NAMES, dry_run=True, deps=deps_factory())
    assert rc == 0
    assert (project.backlog_dir / "keyword_backlog.json").read_text() == before


def test_run_log_tags_each_step_with_its_agent_name(project, deps_factory):
    run_selected_steps(project, run_date="2026-05-19",
                       step_names=STEP_NAMES, dry_run=True, deps=deps_factory())
    log = (project.runs_dir / "2026-05-19" / "run.log").read_text()
    # map "=== step <name> ===" lines to the agent column (2nd field)
    seen = {}
    for line in log.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 4 and parts[3].startswith("=== step "):
            name = parts[3].split()[2]
            seen[name] = parts[1]
    assert seen, "no step lines found in run.log"
    for name, agent in seen.items():
        assert agent == name, f"{name} line tagged as {agent!r}"


def test_reconcile_floor_and_cap(tmp_path):
    path = tmp_path / "keyword_backlog.json"
    path.write_text(json.dumps({"candidates": []}))
    incoming = [{"keyword": f"kw {i}", "score": i / 100} for i in range(100)]
    stats = _reconcile_keyword_backlog(
        path, candidates=incoming, surplus=[], published_keyword="",
        published_set=set(), floor=0.4, cap=10, run_date="2026-05-19")
    kept = json.loads(path.read_text())["candidates"]
    assert len(kept) == 10                      # cap honored
    assert all(c["score"] >= 0.4 for c in kept)  # floor honored
    assert kept[0]["score"] == 0.99             # top-by-score first
    assert stats["kept"] == 10


def _ns(**kw):
    base = {"steps": None, "from_step": None, "stop_after": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_resolve_steps_full_run_when_no_flags():
    assert _resolve_steps(_ns()) is None


def test_resolve_steps_explicit_and_ranges():
    assert _resolve_steps(_ns(steps="researcher,writer")) == ["researcher",
                                                              "writer"]
    assert _resolve_steps(_ns(from_step="outliner")) == STEP_NAMES[2:]
    assert _resolve_steps(_ns(stop_after="writer")) == STEP_NAMES[:4]
    assert _resolve_steps(_ns(from_step="outliner",
                              stop_after="editor")) == ["outliner", "writer",
                                                        "editor"]

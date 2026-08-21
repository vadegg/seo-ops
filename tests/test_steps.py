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
        step_names=["outliner", "writer", "editor", "humanizer",
                    "assembler", "publisher"],
        dry_run=True, deps=deps)
    assert rc == 0
    s = _store(project)
    for name in (A.OUTLINER, A.WRITER, A.EDITOR_MD, A.HUMANIZER,
                 A.ASSEMBLER, A.PUBLISHER):
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


def test_force_overwrites_researcher_not_appends(project, deps_factory):
    """#9 invariant: --force re-runs the Researcher and OVERWRITES 01 with a
    single fresh object — it never accumulates/appends candidates."""
    run_selected_steps(project, run_date="2026-05-19",
                       step_names=["researcher"], dry_run=True,
                       deps=deps_factory())
    first = json.loads(_store(project).path(A.RESEARCHER).read_text())

    run_selected_steps(project, run_date="2026-05-19",
                       step_names=["researcher"], dry_run=True, force=True,
                       deps=deps_factory())
    second = json.loads(_store(project).path(A.RESEARCHER).read_text())

    # still one object, candidate count unchanged (overwrite, not append)
    assert isinstance(second, dict)
    assert len(second["candidates"]) == len(first["candidates"])


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


def test_reconcile_drops_rephrased_variants_of_published_keywords(tmp_path):
    """A published keyword re-enters the reserve under a slightly different
    phrasing and is then re-proposed forever (the 03.08–21.08.2026 loop).
    Pruning must match on the normalized keyword, not the raw string."""
    path = tmp_path / "keyword_backlog.json"
    path.write_text(json.dumps({"candidates": []}))
    published = "how to detect fake and AI-generated participants in user research"
    incoming = [
        {"keyword": published + " (data quality)", "score": 0.73},
        {"keyword": "How to Detect Fake Participants in User Research", "score": 0.7},
        {"keyword": "detect fake and AI-generated participants in user research",
         "score": 0.69},
        {"keyword": "how to recruit B2B research participants", "score": 0.66},
    ]
    _reconcile_keyword_backlog(
        path, candidates=incoming, surplus=[], published_keyword="",
        published_set={published}, floor=0.4, cap=50, run_date="2026-08-21")
    kept = {c["keyword"] for c in json.loads(path.read_text())["candidates"]}
    assert kept == {"how to recruit B2B research participants"}, kept


def test_reconcile_collapses_near_duplicates_inside_the_reserve(tmp_path):
    """Three phrasings of one topic must not eat three slots of the reserve."""
    path = tmp_path / "keyword_backlog.json"
    path.write_text(json.dumps({"candidates": []}))
    incoming = [
        {"keyword": "how B2B buyers use AI (ChatGPT/Perplexity) to research and "
                    "shortlist vendors", "score": 0.72},
        {"keyword": "how B2B buyers use AI (ChatGPT, Perplexity) to research and "
                    "shortlist vendors", "score": 0.7},
        {"keyword": "how B2B buyers use AI (ChatGPT) to research and shortlist "
                    "vendors", "score": 0.68},
    ]
    _reconcile_keyword_backlog(
        path, candidates=incoming, surplus=[], published_keyword="",
        published_set=set(), floor=0.4, cap=50, run_date="2026-08-21")
    kept = json.loads(path.read_text())["candidates"]
    assert len(kept) == 1, kept
    assert kept[0]["score"] == 0.72     # the best score of the merged group


def test_reconcile_keeps_genuinely_distinct_keywords(tmp_path):
    """Normalization must not collapse different topics that share words."""
    path = tmp_path / "keyword_backlog.json"
    path.write_text(json.dumps({"candidates": []}))
    incoming = [
        {"keyword": "how to run a usability test", "score": 0.7},
        {"keyword": "how to run a diary study", "score": 0.69},
        {"keyword": "how much does user research cost", "score": 0.68},
    ]
    _reconcile_keyword_backlog(
        path, candidates=incoming, surplus=[], published_keyword="",
        published_set={"how to run a card sort"}, floor=0.4, cap=50,
        run_date="2026-08-21")
    kept = json.loads(path.read_text())["candidates"]
    assert len(kept) == 3, kept


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


def test_stage_2_loosens_the_gsc_impression_floor(project, deps_factory):
    """Stage 2's job is "loosen GSC thresholds". It used to widen only the
    position band and keep the client's impressions>=20 default, which on a
    young blog leaves ~1 query — so the Researcher fell back to the keyword
    reserve and recycled it."""
    from pipeline.escalation import STAGES
    from pipeline.steps import gather_research_context

    seen = []

    class RecordingGSC:
        def near_top_queries(self, **kw):
            seen.append(kw)
            return [{"query": "q", "clicks": 0, "impressions": 5,
                     "ctr": 0.0, "position": 12.0}]

    class NoDFS:
        def keyword_metrics(self, seeds):
            return []

    deps = deps_factory(gsc=RecordingGSC(), dfs=NoDFS())
    for stage in (1, 2):
        gather_research_context(project, deps, STAGES[stage], logger=None)
    assert seen[0]["min_pos"] == 5.0 and seen[0]["max_pos"] == 20.0
    assert seen[1]["min_pos"] == 3.0 and seen[1]["max_pos"] == 40.0
    assert seen[1]["min_impressions"] < seen[0].get("min_impressions", 20)

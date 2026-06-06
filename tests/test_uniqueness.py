"""#37 uniqueness check (deterministic, no external API).

The check sits between Editor and Assembler. It MinHash-estimates the
Jaccard similarity of the post body against already-published posts and
logs a WARN when the best match exceeds the configured threshold. It must
never hard-block: the post is always passed through to the Assembler.
"""

import json

from pipeline import artifacts as A
from pipeline.artifacts import ArtifactStore
from pipeline.orchestrator import run_selected_steps
from pipeline.uniqueness import (best_match, estimate_similarity,
                                 published_corpus)

# A short, distinctive post body reused across tests.
NOVEL = (
    "Switching costs decide whether a B2B buyer ever migrates. Map the "
    "integrations, the retraining, and the political capital a champion "
    "must spend internally before a competing vendor is even evaluated. "
    "Most teams underestimate the retraining line item by an order of "
    "magnitude, and that is where the deal quietly dies."
)
NEAR_DUP = (
    "Switching costs decide whether a B2B buyer ever migrates. Map the "
    "integrations, the retraining, and the political capital a champion "
    "must spend internally before a competing vendor is even evaluated. "
    "Most teams underestimate the retraining line item by a wide margin, "
    "and that is exactly where the deal quietly dies in the end."
)
UNRELATED = (
    "Card sorting reveals how users group concepts in their own mental "
    "model. Run an open sort first to surface the vocabulary, then a "
    "closed sort to validate a proposed information architecture against "
    "real navigation expectations across distinct user segments."
)


# ---- algorithm -------------------------------------------------------------
def test_identical_text_is_max_similarity():
    assert estimate_similarity(NOVEL, NOVEL) > 0.95


def test_near_duplicate_scores_above_threshold():
    # Two phrase edits in a ~50-word body still land above the 0.55 default
    # threshold and far above an unrelated post — the signal we care about.
    s = estimate_similarity(NOVEL, NEAR_DUP)
    assert s > 0.55, f"near-duplicate should clear the threshold, got {s}"


def test_unrelated_text_scores_low():
    s = estimate_similarity(NOVEL, UNRELATED)
    assert s < 0.2, f"unrelated text should score low, got {s}"


def test_best_match_picks_the_closest_corpus_entry():
    corpus = [
        {"slug": "card-sorting", "body": UNRELATED},
        {"slug": "switching-costs", "body": NEAR_DUP},
    ]
    score, entry = best_match(NOVEL, corpus)
    assert entry["slug"] == "switching-costs"
    assert score > 0.55


def test_empty_corpus_scores_zero():
    score, entry = best_match(NOVEL, [])
    assert score == 0.0
    assert entry is None


# ---- corpus source ---------------------------------------------------------
def test_published_corpus_reads_topic_history_bodies(tmp_path):
    th = {"published": [
        {"slug": "a", "topic": "T", "body": UNRELATED},
        {"slug": "b", "topic": "T2"},  # no body -> skipped
    ]}
    (tmp_path / "topic_history.json").write_text(json.dumps(th))
    corpus = published_corpus(tmp_path / "topic_history.json")
    assert [c["slug"] for c in corpus] == ["a"]


# ---- step integration ------------------------------------------------------
def _seed_through_editor(project, deps_factory, body):
    run_selected_steps(project, run_date="2026-05-19",
                       step_names=["researcher", "strategist", "outliner",
                                   "writer", "editor"],
                       dry_run=True, deps=deps_factory())
    ArtifactStore(project.runs_dir / "2026-05-19").write_text(A.EDITOR_MD, body)


def _run_uniqueness(project, deps_factory):
    deps = deps_factory()
    rc = run_selected_steps(project, run_date="2026-05-19",
                            step_names=["uniqueness"], dry_run=True, deps=deps)
    return rc


def _runlog(project):
    return (project.runs_dir / "2026-05-19" / "run.log").read_text()


def test_step_writes_artifact_and_score(project, deps_factory):
    _seed_through_editor(project, deps_factory, NOVEL)
    rc = _run_uniqueness(project, deps_factory)
    assert rc == 0
    store = ArtifactStore(project.runs_dir / "2026-05-19")
    assert store.exists(A.UNIQUENESS)
    art = store.read_json(A.UNIQUENESS)
    assert "max_similarity" in art
    assert 0.0 <= art["max_similarity"] <= 1.0


def test_near_duplicate_logs_warn(project, deps_factory):
    # Seed a published post, then feed a near-duplicate body to the editor.
    th_path = project.backlog_dir / "topic_history.json"
    th_path.write_text(json.dumps({"published": [
        {"slug": "switching-costs", "topic": "Switching costs",
         "body": NOVEL}]}), encoding="utf-8")
    _seed_through_editor(project, deps_factory, NEAR_DUP)
    rc = _run_uniqueness(project, deps_factory)
    assert rc == 0
    log = _runlog(project)
    assert "uniqueness" in log.lower()
    assert "similar" in log.lower()
    # the matched slug is surfaced for triage
    assert "switching-costs" in log
    # score is recorded in the artifact telemetry
    store = ArtifactStore(project.runs_dir / "2026-05-19")
    assert store.read_json(A.UNIQUENESS)["max_similarity"] > 0.55


def test_novel_post_does_not_warn(project, deps_factory):
    th_path = project.backlog_dir / "topic_history.json"
    th_path.write_text(json.dumps({"published": [
        {"slug": "card-sorting", "topic": "Card sorting",
         "body": UNRELATED}]}), encoding="utf-8")
    _seed_through_editor(project, deps_factory, NOVEL)
    rc = _run_uniqueness(project, deps_factory)
    assert rc == 0
    store = ArtifactStore(project.runs_dir / "2026-05-19")
    assert store.read_json(A.UNIQUENESS)["max_similarity"] < 0.2
    # no WARN/ERROR line attributed to the uniqueness step
    for line in _runlog(project).splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 4 and parts[1] == "uniqueness":
            assert parts[2] not in {"WARNING", "ERROR"}, line


def test_step_never_blocks_pipeline(project, deps_factory):
    # Even an exact duplicate still lets humanizer+assembler+publisher run.
    th_path = project.backlog_dir / "topic_history.json"
    th_path.write_text(json.dumps({"published": [
        {"slug": "switching-costs", "topic": "x", "body": NOVEL}]}),
        encoding="utf-8")
    _seed_through_editor(project, deps_factory, NOVEL)
    deps = deps_factory()
    rc = run_selected_steps(
        project, run_date="2026-05-19",
        step_names=["uniqueness", "humanizer", "assembler", "publisher"],
        dry_run=True, deps=deps)
    assert rc == 0
    store = ArtifactStore(project.runs_dir / "2026-05-19")
    assert store.exists(A.ASSEMBLER)
    assert store.exists(A.PUBLISHER)

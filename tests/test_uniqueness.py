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


# ---- corpus source: the blog clone, not topic_history ----------------------
# topic_history entries carry no ``body`` in production, so a corpus built
# from them alone is always empty (corpus_size: 0 in every run 05.2026–08.2026)
# and the guard never fires. The real corpus is the cloned Astro content
# collection the Publisher writes into.
def _seed_blog_clone(project, files: dict[str, str]) -> "Path":
    from pathlib import Path
    d = (Path(project.runs_dir) / "_blog_repo" / project.blog_posts_dir)
    d.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (d / name).write_text(body, encoding="utf-8")
    return d


def test_published_corpus_reads_the_blog_content_collection(project, tmp_path):
    d = _seed_blog_clone(project, {
        "2026-05-19-switching-costs.md":
            "---\ntitle: Switching costs\nslug: switching-costs\n---\n\n" + NOVEL})
    th = tmp_path / "topic_history.json"
    th.write_text(json.dumps({"published": []}), encoding="utf-8")
    corpus = published_corpus(th, blog_content_dir=d)
    assert len(corpus) == 1
    assert corpus[0]["slug"] == "switching-costs"
    assert "Switching costs decide" in corpus[0]["body"]


def test_published_corpus_excludes_todays_own_post(project, tmp_path):
    d = _seed_blog_clone(project, {
        "2026-05-19-today.md": "---\nslug: today\n---\n\n" + NOVEL,
        "2026-05-01-older.md": "---\nslug: older\n---\n\n" + UNRELATED})
    th = tmp_path / "topic_history.json"
    th.write_text(json.dumps({"published": []}), encoding="utf-8")
    corpus = published_corpus(th, blog_content_dir=d,
                              exclude_prefix="2026-05-19-")
    assert [c["slug"] for c in corpus] == ["older"]


def test_step_warns_on_near_duplicate_of_a_post_in_the_blog_clone(
        project, deps_factory):
    """The regression that let one topic ship eight times: history has no
    bodies, so the guard must read the clone to see the published post."""
    _seed_blog_clone(project, {
        "2026-05-01-switching-costs.md":
            "---\nslug: switching-costs\n---\n\n" + NOVEL})
    _seed_through_editor(project, deps_factory, NEAR_DUP)
    assert _run_uniqueness(project, deps_factory) == 0
    art = ArtifactStore(project.runs_dir / "2026-05-19").read_json(A.UNIQUENESS)
    assert art["corpus_size"] == 1
    assert art["max_similarity"] > 0.55
    assert art["above_threshold"] is True
    assert "switching-costs" in _runlog(project)


def test_step_warns_when_the_corpus_is_empty(project, deps_factory):
    """A missing/empty corpus scores 0.0 for everything — the guard is off.
    That must be visible in the run telemetry, not silently 'ok'."""
    _seed_through_editor(project, deps_factory, NOVEL)
    assert _run_uniqueness(project, deps_factory) == 0
    art = ArtifactStore(project.runs_dir / "2026-05-19").read_json(A.UNIQUENESS)
    assert art["corpus_size"] == 0
    log = _runlog(project)
    assert "INACTIVE" in log
    assert "WARNING" in log

"""Dedupe guard: what the Researcher/Strategist actually see as "already
published".

topic_history.json is append-only (oldest first). The prompt renderer must
drop the OLDEST entries when it runs out of budget — truncating the newest
ones hides exactly the posts most likely to be re-proposed, which is how the
same topic shipped eight days in a row (03.08–21.08.2026).
"""

import json

from agents import history, researcher, strategist


def _history(n: int) -> dict:
    return {"published": [
        {"topic": f"Topic number {i} about research methods and practice",
         "keyword": f"keyword number {i}",
         "slug": f"slug-{i}", "date": f"2026-01-{(i % 28) + 1:02d}"}
        for i in range(n)]}


class CapturingRunner:
    def __init__(self, canned: str):
        self.user = None
        self._canned = canned

    def run(self, *, name, system, user, model, tools, max_tokens, logger):
        self.user = user
        return self._canned


# ---- renderer --------------------------------------------------------------
def test_render_keeps_newest_and_drops_oldest_when_over_budget():
    rendered = history.render_published(_history(200), budget=1000)
    assert "Topic number 199" in rendered, "newest entry must survive"
    assert "Topic number 0 " not in rendered, "oldest should be dropped first"


def test_render_reports_how_many_were_omitted():
    rendered = history.render_published(_history(200), budget=1000)
    assert "omitted" in rendered.lower()


def test_render_surfaces_keyword_when_it_differs_from_topic():
    th = {"published": [{"topic": "Detecting fake participants",
                         "keyword": "how to detect fake participants",
                         "date": "2026-08-03"}]}
    rendered = history.render_published(th)
    assert "Detecting fake participants" in rendered
    assert "how to detect fake participants" in rendered


def test_render_handles_empty_history():
    assert history.render_published({"published": []}).strip()
    assert history.render_published(None).strip()


# ---- agents ----------------------------------------------------------------
def test_researcher_prompt_contains_the_most_recent_published_topic():
    r = CapturingRunner(json.dumps({
        "candidates": [{"keyword": "k", "intent": "informational",
                        "rationale": "r", "source": "gsc",
                        "est_difficulty": "low", "score": 0.5,
                        "supporting_data": "d"}],
        "backlog_surplus": []}))
    researcher.run(r, model="m", tools=[], max_tokens=100, logger=None,
                   stage_spec=type("S", (), {"stage": 1, "approach": "a",
                                             "use_websearch": False,
                                             "use_seed_list": False})(),
                   backlog={"candidates": []}, topic_history=_history(200),
                   gsc_rows=[], dfs_metrics=[], seed_topics="")
    assert "Topic number 199" in r.user


def test_strategist_prompt_contains_the_most_recent_published_topic():
    r = CapturingRunner(json.dumps({
        "topic": "t", "primary_keyword": "k", "cluster": "c",
        "angle": "a", "score": 0.9, "rationale": "r",
        "secondary_keywords": [], "search_intent": "informational"}))
    strategist.run(r, model="m", tools=[], max_tokens=100, logger=None,
                   candidates={"candidates": []}, topic_history=_history(200),
                   content_map="")
    assert "Topic number 199" in r.user


# ---- topic-level guard -----------------------------------------------------
# Text-level uniqueness (MinHash) only catches paraphrase: the eight
# same-topic posts of 03.08–21.08.2026 score ~0.01 against each other because
# they were written from scratch every time. The guard that would have stopped
# them compares the *topic*, right after the Strategist picks it.
def test_duplicate_of_published_matches_a_rephrased_keyword():
    from pipeline.dedupe import duplicate_of_published

    th = {"published": [
        {"topic": "How to Detect Fake and AI-Generated Participants in User "
                  "Research",
         "keyword": "how to detect fake and AI-generated participants in user "
                    "research", "slug": "detect-fake-ai-participants"}]}
    hit = duplicate_of_published(
        {"topic": "How to Detect Fake Participants in User Research: A "
                  "Data-Quality Playbook",
         "primary_keyword": "how to detect fake participants in user research"},
        th)
    assert hit and hit["slug"] == "detect-fake-ai-participants"


def test_duplicate_of_published_passes_a_genuinely_new_topic():
    from pipeline.dedupe import duplicate_of_published

    th = {"published": [{"topic": "Concept testing", "keyword": "concept testing",
                         "slug": "concept-testing"}]}
    assert duplicate_of_published(
        {"topic": "Continuous discovery in product teams",
         "primary_keyword": "continuous discovery in product teams"}, th) is None

"""Shared fixtures + fakes. Tests never touch the network or the SDK."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from clients.retry import ClientError  # noqa: E402


# --------------------------------------------------------------------------
# Fake agent runner — returns canned, schema-valid output keyed by agent.
# --------------------------------------------------------------------------
class FakeRunner:
    def __init__(self, strategist_score: float = 0.9):
        self.score = strategist_score
        self.calls: list[str] = []

    def run(self, *, name, system, user, model, tools, max_tokens, logger):
        self.calls.append(name)
        if name == "researcher":
            return json.dumps({
                "candidates": [
                    {"keyword": "usability testing sample size",
                     "intent": "informational",
                     "rationale": "striking distance, high intent",
                     "source": "gsc", "est_difficulty": "low", "score": 0.85,
                     "supporting_data": "pos 8, 400 impressions"},
                    {"keyword": "remote moderated usability testing",
                     "intent": "informational", "rationale": "adjacent cluster",
                     "source": "dataforseo", "est_difficulty": "medium",
                     "score": 0.6, "supporting_data": "vol 300"},
                    {"keyword": "ux survey tools 2020",
                     "intent": "commercial", "rationale": "stale, low value",
                     "source": "websearch", "est_difficulty": "high",
                     "score": 0.2, "supporting_data": "dated"}],
                "backlog_surplus": [
                    {"keyword": "card sorting guide", "score": 0.7}]})
        if name == "strategist":
            return json.dumps({
                "topic": "How many users for usability testing",
                "primary_keyword": "usability testing sample size",
                "secondary_keywords": ["how many users", "nielsen 5 users"],
                "search_intent": "informational",
                "cluster": "usability testing (moderated vs unmoderated)",
                "pillar_hub_slug": "ux-research-methods",
                "angle": "Why 5 is a starting point, not a rule",
                "score": self.score,
                "rationale": "fills an open cluster, low difficulty"})
        if name == "outliner":
            return json.dumps({
                "title": "How Many Users for Usability Testing",
                "slug": "usability-testing-sample-size",
                "meta_description": ("How many users you actually need for "
                                     "usability testing, when the five-user "
                                     "rule is enough, and when it quietly "
                                     "fails you — a practical, example-led "
                                     "guide."),
                "primary_keyword": "usability testing sample size",
                "secondary_keywords": ["how many users", "nielsen 5 users"],
                "target_word_count": 1200,
                "jsonld_type": "BlogPosting",
                "sections": [{"h2": "The 5-user rule, in context",
                              "key_points": ["origin", "limits"],
                              "word_count": 600,
                              "internal_links": [{
                                  "anchor": "research methods",
                                  "url": "/blog/ux-research-methods"}]}],
                "faq": [{"q": "Is 5 users always enough?",
                         "a_outline": "No — depends on task count."}],
                "hero_image_alt": "Researcher observing a usability test"})
        if name == "writer":
            return ("## The 5-user rule, in context\n\n"
                    "The widely cited five-user figure comes from a specific "
                    "model, not a universal law. Read the [research methods]"
                    "(/blog/ux-research-methods) hub for the bigger picture.\n\n"
                    "![](/img/test.png)\n")
        if name == "editor":
            return json.dumps({
                "edited_markdown": (
                    "## The 5-user rule, in context\n\n"
                    "The widely cited five-user figure comes from a specific "
                    "cost-benefit model, not a universal law. See our "
                    "[research methods](/blog/ux-research-methods) hub.\n\n"
                    "![Researcher observing a usability test](/img/test.png)\n"),
                "critique": {"checklist": {
                    "on_brief": True, "style_guide": True, "seo": True,
                    "internal_links": True, "evidence_grounded": True,
                    "no_client_leak": True}, "passed": True,
                    "notes": "clean"}})
        raise AssertionError(f"unexpected agent {name}")


class FailingEditorRunner(FakeRunner):
    """Editor never passes the checklist (forces Opus final + hard alert)."""

    def run(self, *, name, **kw):
        if name == "editor":
            self.calls.append(name)
            return json.dumps({
                "edited_markdown": "## Body\n\nText.\n",
                "critique": {"checklist": {
                    "on_brief": True, "style_guide": False, "seo": True,
                    "internal_links": True, "evidence_grounded": True,
                    "no_client_leak": True}, "passed": False,
                    "notes": "style issues"}})
        return super().run(name=name, **kw)


# --------------------------------------------------------------------------
# Fake external clients
# --------------------------------------------------------------------------
class FakeGSC:
    def __init__(self, fail=False):
        self.fail = fail

    def near_top_queries(self, **kw):
        if self.fail:
            raise ClientError("gsc down")
        return [{"query": "usability testing sample size", "clicks": 3,
                 "impressions": 400, "ctr": 0.0075, "position": 8.0}]


class FakeDFS:
    def __init__(self, fail=False):
        self.fail = fail

    def keyword_metrics(self, keywords):
        if self.fail:
            raise ClientError("dataforseo down")
        return [{"keyword": k, "search_volume": 320, "competition": 0.2,
                 "cpc": 1.1} for k in keywords]

    def serp_top(self, keyword, depth=10):
        return []


class FakeEvidence:
    def __init__(self, passages=None):
        self._p = passages or [
            {"file": "ux-notes.md",
             "text": "Across studies, task coverage matters more than raw "
                     "participant count.", "score": 1.0}]

    def search(self, query, k=8):
        return self._p[:k]


class FakeGit:
    def __init__(self):
        self.pushed = False
        self.written: dict[str, str] = {}

    def ensure_clone(self):
        pass

    def write_post(self, rel_path, content):
        self.written[rel_path] = content
        return Path(rel_path)

    def commit_and_push(self, rel_paths, message, push=True):
        self.pushed = push
        return "deadbeef"


class FakeTelegram:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def send(self, text, level="info"):
        self.messages.append((level, text))


@pytest.fixture
def project(tmp_path) -> Config:
    """A Config rooted in a tmp copy of the persistent stores."""
    for rel in ("backlog/keyword_backlog.json", "backlog/topic_history.json",
                "backlog/seed_topics.md", "themes/content_map.md",
                "themes/internal_links.json", "style_guide.md"):
        src = ROOT / rel
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / "notes.md").write_text(
        "Task coverage matters more than participant count.\n",
        encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    key = tmp_path / "secrets" / "k"
    key.write_text("x", encoding="utf-8")

    return Config(
        anthropic_api_key="test", gsc_service_account_json=key,
        gsc_site_url="sc-domain:example.com", dataforseo_login="l",
        dataforseo_password="p", telegram_bot_token="t",
        telegram_chat_id="c", blog_repo_url="git@example:repo.git",
        git_deploy_key=key, blog_branch="main",
        blog_posts_dir="src/content/blog",
        blog_base_url="https://glasgowresearch.com/blog",
        evidence_dir=evidence_dir, project_root=tmp_path)


@pytest.fixture
def deps_factory():
    from pipeline.orchestrator import PipelineDeps

    def make(runner=None, gsc=None, dfs=None, evidence=None,
             git=None, tg=None):
        return PipelineDeps(
            agent_runner=runner or FakeRunner(),
            gsc=gsc or FakeGSC(), dataforseo=dfs or FakeDFS(),
            evidence=evidence or FakeEvidence(), git=git or FakeGit(),
            telegram=tg or FakeTelegram())

    return make

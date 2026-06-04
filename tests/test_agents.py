"""Agent prompt contracts: contextual linking (#13), first-hand (#14)."""

from agents import writer
from pipeline.steps import select_relevant_links


class CapturingRunner:
    """Records the user prompt it is handed and returns a canned body."""

    def __init__(self):
        self.user = None

    def run(self, *, name, system, user, model, tools, max_tokens, logger):
        self.user = user
        return "## Body\n\nText.\n"


# ---- #13 contextual internal linking --------------------------------------
def test_select_relevant_links_prefers_cluster_overlap():
    links = {"hubs": {"h": "/h"}, "posts": [
        {"cluster": "usability testing", "title": "Guerrilla Usability",
         "url": "/u", "date": "2026-05-01"},
        {"cluster": "pricing research", "title": "Pricing for SaaS",
         "url": "/p", "date": "2026-05-02"},
    ]}
    topic = {"cluster": "usability testing", "primary_keyword": "usability",
             "secondary_keywords": ["testing"]}
    out = select_relevant_links(links, topic)
    assert out["hubs"] == {"h": "/h"}
    assert out["posts"][0]["url"] == "/u"   # cluster-matching post ranks first


def test_select_relevant_links_falls_back_when_no_overlap():
    links = {"posts": [{"cluster": "x", "title": "y", "url": "/a",
                        "date": "2026-01-01"}]}
    out = select_relevant_links(links, {"cluster": "completely unrelated"})
    assert out["posts"]  # still offers something rather than nothing


# ---- #14 first-hand evidence block ----------------------------------------
def test_writer_requires_first_hand_when_evidence_present():
    r = CapturingRunner()
    writer.run(r, model="m", tools=[], max_tokens=100, logger=None,
               brief={"title": "t"}, style_guide="",
               evidence_passages=[{"file": "n.md", "text": "insight"}])
    assert "first-hand" in r.user.lower()
    assert "AT LEAST ONE" in r.user


def test_writer_forbids_first_hand_when_no_evidence():
    r = CapturingRunner()
    writer.run(r, model="m", tools=[], max_tokens=100, logger=None,
               brief={"title": "t"}, style_guide="", evidence_passages=[])
    assert "Do NOT invent first-hand" in r.user

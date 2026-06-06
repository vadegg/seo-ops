"""#39 Humanizer — reduce AI patterns in text.

Two layers, both tested with no network/API:
  * a DETERMINISTIC stop-phrase filter (``strip_cliches``) that removes
    cliché connectives/openers while preserving facts + markdown links;
  * an LLM rewrite pass (``humanizer.run``) that mirrors the editor
    contract and is NON-BLOCKING: on any runner failure it returns the
    input unchanged and never raises.
"""

from __future__ import annotations

from agents import humanizer
from clients.retry import ClientError


SAMPLE = (
    "## Why sample size matters\n\n"
    "In today's fast-paced world, research moves quickly. Moreover, the "
    "five-user rule is widely cited. It's important to note that 5 users "
    "find roughly 85% of problems. Furthermore, see our "
    "[research methods](/blog/ux-research-methods) hub for context.\n\n"
    "In conclusion, plan for 5-8 participants per round.\n"
)


# ---- (a) deterministic cliché removal --------------------------------------
def test_strip_cliches_removes_known_phrases():
    out = humanizer.strip_cliches(SAMPLE)
    low = out.lower()
    for phrase in ("in today's fast-paced world", "moreover",
                   "it's important to note", "furthermore", "in conclusion"):
        assert phrase not in low, f"{phrase!r} survived"


def test_cliche_list_is_the_single_source_of_truth():
    # The list is generously seeded and lives in one place.
    assert len(humanizer.AI_CLICHES) >= 15
    assert any("fast-paced world" in c.lower() for c in humanizer.AI_CLICHES)
    assert any(c.lower() == "moreover" for c in humanizer.AI_CLICHES)


# ---- (b) facts + internal links preserved ----------------------------------
def test_strip_cliches_preserves_facts_and_links():
    out = humanizer.strip_cliches(SAMPLE)
    # the markdown internal link survives verbatim
    assert "[research methods](/blog/ux-research-methods)" in out
    # concrete facts/numbers survive
    assert "85%" in out
    assert "5-8 participants" in out
    assert "five-user rule" in out


def test_strip_cliches_keeps_sentences_starting_capitalised():
    # Removing a leading connective must leave a clean, capitalised sentence.
    out = humanizer.strip_cliches("Moreover, the rule is a heuristic.")
    assert out == "The rule is a heuristic."


# ---- (c) idempotent --------------------------------------------------------
def test_strip_cliches_is_idempotent():
    once = humanizer.strip_cliches(SAMPLE)
    twice = humanizer.strip_cliches(once)
    assert once == twice


# ---- (d) non-blocking LLM fallback -----------------------------------------
class _RaisingRunner:
    def run(self, **kw):
        raise ClientError("model down")


class _CapturingRunner:
    def __init__(self):
        self.user = None
        self.system = None

    def run(self, *, name, system, user, model, tools, max_tokens, logger):
        self.user = user
        self.system = system
        return ("## Why sample size matters\n\n"
                "Sample size is not a formality. The five-user rule is a "
                "heuristic: 5 users surface about 85% of problems. We lean on "
                "our [research methods](/blog/ux-research-methods) hub for "
                "context.\n\nPlan for 5-8 participants per round.\n")


def test_run_returns_input_unchanged_on_runner_failure():
    body = SAMPLE
    out = humanizer.run(_RaisingRunner(), model="m", tools=[], max_tokens=100,
                        logger=None, draft_md=body, style_guide="",
                        evidence_passages=[])
    assert out == body  # non-blocking: failure must not break the run


def test_run_rewrites_and_anchors_to_style_guide():
    r = _CapturingRunner()
    out = humanizer.run(r, model="m", tools=[], max_tokens=500, logger=None,
                        draft_md=SAMPLE, style_guide="GLASGOW VOICE MARKER",
                        evidence_passages=[{"file": "n.md", "text": "insight"}])
    # prompt is anchored to the style guide and lists clichés to avoid
    assert "GLASGOW VOICE MARKER" in r.user
    assert "moreover" in r.system.lower() or "moreover" in r.user.lower()
    # internal link preserved through the rewrite
    assert "[research methods](/blog/ux-research-methods)" in out


def test_run_empty_llm_output_falls_back_to_deterministic():
    class _EmptyRunner:
        def run(self, **kw):
            return "   "

    out = humanizer.run(_EmptyRunner(), model="m", tools=[], max_tokens=100,
                        logger=None, draft_md=SAMPLE, style_guide="",
                        evidence_passages=[])
    # never empty; clichés still stripped deterministically
    assert out.strip()
    assert "moreover" not in out.lower()

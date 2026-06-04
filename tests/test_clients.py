import pytest

from clients.evidence import EvidenceClient
from clients.retry import ClientError, with_backoff


def test_evidence_bm25_ranks_relevant_first(tmp_path):
    (tmp_path / "a.md").write_text(
        "Sample size for usability testing depends on task coverage.\n\n"
        "An unrelated paragraph about coffee brewing methods entirely.\n",
        encoding="utf-8")
    ev = EvidenceClient(tmp_path)
    hits = ev.search("usability testing sample size", k=2)
    assert hits
    assert "usability" in hits[0]["text"].lower()
    assert hits[0]["file"] == "a.md"


def test_evidence_empty_dir(tmp_path):
    assert EvidenceClient(tmp_path).search("anything") == []


def test_backoff_raises_clienterror_after_attempts():
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("nope")

    with pytest.raises(ClientError):
        with_backoff(boom, attempts=3, base_delay=0, max_delay=0,
                     sleep=lambda _: None, label="x")
    assert len(calls) == 3


def test_backoff_succeeds_on_retry():
    state = {"n": 0}

    def flaky():
        state["n"] += 1
        if state["n"] < 2:
            raise RuntimeError("transient")
        return "ok"

    assert with_backoff(flaky, attempts=3, base_delay=0,
                        sleep=lambda _: None) == "ok"

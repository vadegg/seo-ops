import copy
import json

import pytest

from agents.validation import ValidationError, validate_outliner
from pipeline.dedupe import duplicate_of_published
from pipeline.quality import QualityError, require_source_citations
from tests.conftest import FakeRunner


def brief():
    return json.loads(FakeRunner()._canned(name="outliner", model="test"))


def test_missing_sources_or_distinct_reader_task_requires_a_new_brief():
    for field in ("sources", "original_value"):
        data = brief()
        del data[field]
        with pytest.raises(ValidationError):
            validate_outliner(data)
    data = brief()
    data["original_value"]["difference"] = ""
    with pytest.raises(ValidationError):
        validate_outliner(data)


def test_source_citation_must_survive_final_body_and_match_the_brief():
    data = brief()
    url = data["sources"][0]["url"]
    body = f"## Plan the study\n\nUse [iterative testing]({url})."
    require_source_citations(data, body)
    with pytest.raises(QualityError, match="not cited"):
        require_source_citations(data, "## Plan the study\n\nThe link was removed.")
    with pytest.raises(QualityError, match="missing from verified brief"):
        require_source_citations(data, body + " [Unsupported](https://example.com/claim)")
    old = copy.deepcopy(data)
    del old["sources"]
    with pytest.raises(QualityError, match="brief requires revision"):
        require_source_citations(old, body)


def test_source_check_cannot_be_faked_with_future_date_or_non_https_url():
    for key, value in (("checked_on", "2099-01-01"), ("url", "file:///tmp/private")):
        data = brief()
        data["sources"][0][key] = value
        with pytest.raises(ValidationError):
            validate_outliner(data)


def test_reviewed_intents_catch_new_wording_but_allow_specialist_questions():
    history = {"published": [{"keyword": "thematic analysis qualitative research", "slug": "existing"}]}
    assert duplicate_of_published({"primary_keyword": "thematic analysis in UX research"}, history)
    assert not duplicate_of_published({"primary_keyword": "reflexive vs codebook thematic analysis"}, history)
    history = {"published": [{"keyword": "b2b buyer research methods", "slug": "buyer"}]}
    assert duplicate_of_published({"primary_keyword": "b2b buyer research process"}, history)
    assert not duplicate_of_published({"primary_keyword": "b2b buyers using AI to shortlist vendors"}, history)

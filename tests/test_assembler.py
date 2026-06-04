import re

import pytest

from pipeline.assembler import AssemblyError, assemble, scrub_confidential

# A meta description that normalizes within the blog's 150–160 Zod refine.
VALID_DESC = (
    "A practical, evidence-led guide to picking the research method that "
    "fits the decision you actually face, with concrete examples for B2B "
    "SaaS product teams now."
)


def _brief(**over):
    b = {"title": "How Many Users for Usability Testing",
         "slug": "usability-testing-sample-size",
         "meta_description": VALID_DESC,
         "primary_keyword": "usability testing sample size",
         "secondary_keywords": ["how many users"],
         "jsonld_type": "BlogPosting", "faq": [],
         "hero_image_alt": "A researcher"}
    b.update(over)
    return b


def _assemble(brief):
    return assemble(edited_markdown="## Body\n\n![](/i.png)\n", brief=brief,
                    topic={"topic": "x", "cluster": "usability testing"},
                    internal_links={}, site_name="Glasgow Research",
                    base_url="https://blog.glasgow.works/blog",
                    run_date="2026-05-19", author_name="Vadim Glazkov",
                    author_slug="vadim", default_category="Research")


def test_scrub_removes_confidential_lines():
    text = ("Normal line.\n"
            "This paragraph is CONFIDENTIAL — client X data.\n"
            "Another normal line.\n")
    clean, hits = scrub_confidential(text)
    assert "CONFIDENTIAL" not in clean.upper()
    assert len(hits) == 1
    assert "Normal line." in clean


def test_assemble_frontmatter_and_jsonld():
    post = _assemble(_brief())
    md = post.markdown
    assert md.startswith("---\n")
    assert "title: \"How Many Users for Usability Testing\"" in md
    assert "pubDate: 2026-05-19" in md
    assert 'application/ld+json' in md
    assert '"@type": "BlogPosting"' in md
    assert "![A researcher](/i.png)" in md
    assert post.slug == "usability-testing-sample-size"
    assert post.leaked is False


def test_frontmatter_has_required_schema_fields():
    """Astro content collection requires author + authorSlug; description
    must be 150–160 chars. Missing these breaks the Cloudflare build."""
    md = _assemble(_brief()).markdown
    assert 'author: "Vadim Glazkov"' in md
    assert 'authorSlug: "vadim"' in md
    assert 'category: "Research"' in md
    m = re.search(r'^description: "(.*)"$', md, re.MULTILINE)
    assert m is not None
    assert 150 <= len(m.group(1)) <= 160


def test_category_from_brief_overrides_default():
    md = _assemble(_brief(category="Methods")).markdown
    assert 'category: "Methods"' in md


@pytest.mark.parametrize("desc", ["too short", "x" * 200])
def test_description_out_of_range_raises(desc):
    with pytest.raises(AssemblyError):
        _assemble(_brief(meta_description=desc))


def test_description_whitespace_normalized_for_length_check():
    """Leading/trailing/collapsed whitespace is normalized like the blog's
    normalizeMetaDescription before the length gate."""
    padded = "   " + ("y " * 78).strip() + "   "  # -> 155 chars normalized
    md = _assemble(_brief(meta_description=padded)).markdown
    m = re.search(r'^description: "(.*)"$', md, re.MULTILINE)
    assert 150 <= len(m.group(1)) <= 160
    assert "  " not in m.group(1)  # collapsed

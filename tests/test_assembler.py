import re

import pytest

from pipeline.assembler import (AssemblyError, assemble,
                                _count_internal_links, _toc_block)

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


def _assemble_full(brief, body="## Body\n\n![](/i.png)\n", **over):
    kw = dict(edited_markdown=body, brief=brief,
              topic={"topic": "x", "cluster": "usability testing"},
              internal_links={}, site_name="Glasgow Research",
              base_url="https://blog.glasgow.works/blog",
              run_date="2026-05-19", author_name="Vadim Glazkov",
              author_slug="vadim", default_category="Research",
              cta_text="We help SaaS teams.",
              cta_url="https://glasgow.works",
              tool_disclosure="Disclosure: paid tools may be mentioned.",
              author_same_as=("https://linkedin.com/in/vadim",),
              org_same_as=("https://linkedin.com/company/glasgow",),
              default_og_image="https://blog.glasgow.works/og/default.png")
    kw.update(over)
    return assemble(**kw)


def test_footer_present_and_unique():
    post = _assemble_full(_brief())
    assert post.markdown.count("<!-- gr:footer -->") == 1
    assert "https://glasgow.works" in post.markdown
    # Re-assembling the already-footed body must not double it.
    again = _assemble_full(_brief(), body=post.markdown.split("---\n", 2)[-1])
    assert again.markdown.count("<!-- gr:footer -->") == 1


def test_disclosure_only_when_flagged_or_tools_title():
    # Plain methodology post -> no disclosure.
    assert "<!-- gr:disclosure -->" not in _assemble_full(_brief()).markdown
    # Explicit brief flag -> disclosure present.
    flagged = _assemble_full(_brief(has_tool_recommendations=True))
    assert "<!-- gr:disclosure -->" in flagged.markdown
    assert "Disclosure" in flagged.markdown
    # Tools-round-up title heuristic -> disclosure present.
    tools = _assemble_full(_brief(title="Best UX Research Tools in 2026"))
    assert "<!-- gr:disclosure -->" in tools.markdown


def test_toc_only_for_long_posts():
    short = _assemble_full(_brief())
    assert "<!-- gr:toc -->" not in short.markdown
    long_body = ("## First section\n\n" + ("word " * 700) +
                 "\n\n## Second section\n\n" + ("word " * 700) + "\n")
    long_post = _assemble_full(_brief(), body=long_body)
    assert "<!-- gr:toc -->" in long_post.markdown
    # #33: reading time lives only in frontmatter (template renders it once);
    # never duplicated as an in-body line.
    assert "min read" not in long_post.markdown
    assert "[First section](#first-section)" in long_post.markdown
    assert "[Second section](#second-section)" in long_post.markdown


def test_jsonld_person_author_with_sameas_and_image():
    md = _assemble_full(_brief()).markdown
    assert '"@type": "Person"' in md
    assert '"name": "Vadim Glazkov"' in md
    assert "https://linkedin.com/in/vadim" in md          # author sameAs
    assert "https://linkedin.com/company/glasgow" in md    # org sameAs
    assert '"@type": "ImageObject"' in md
    assert '"width": 1200' in md
    # author must no longer be an Organization
    assert '"author": {\n      "@type": "Organization"' not in md


def test_jsonld_description_matches_normalized_frontmatter():
    # An over-long meta is clamped for frontmatter; the JSON-LD description
    # must use the SAME clamped value, not the raw one (#3 review fix).
    long_desc = ("This is a deliberately over-long meta description that keeps "
                 "going well past the one hundred and sixty character ceiling "
                 "so the assembler has to clamp it down to a fitting size now.")
    md = _assemble_full(_brief(meta_description=long_desc)).markdown
    fm_desc = re.search(r'^description: "(.*)"$', md, re.MULTILINE).group(1)
    jl_desc = re.search(r'"description": "(.*)"', md).group(1)
    assert fm_desc == jl_desc
    assert long_desc not in md          # raw, unclamped value never emitted


def test_count_internal_links_netloc_exact():
    base = "https://blog.glasgow.works/blog"
    # site-relative + same-host absolute count; substring look-alikes do not
    body = ("[a](/blog/x) "
            "[b](https://blog.glasgow.works/blog/y) "
            "[c](https://blog.glasgow.works.evil.example/z) "
            "[d](https://other.com/p?ref=blog.glasgow.works) "
            "[e](//cdn.example/p)")
    assert _count_internal_links(body, base) == 2


def test_toc_dedupes_repeated_heading_anchors():
    body = ("## Examples\n\n" + ("word " * 700) +
            "\n\n## Examples\n\n" + ("word " * 700) + "\n")
    toc = _toc_block(body)
    assert "(#examples)" in toc
    assert "(#examples-1)" in toc      # second identical H2 gets a suffix


def test_underlinked_post_warns(caplog):
    import logging
    links = {"posts": [{"url": f"/p{i}"} for i in range(5)]}
    with caplog.at_level(logging.WARNING):
        assemble(edited_markdown="## Body\n\nNo links here.\n", brief=_brief(),
                 topic={"topic": "x"}, internal_links=links,
                 site_name="Glasgow Research",
                 base_url="https://blog.glasgow.works/blog",
                 run_date="2026-05-19", logger=logging.getLogger("t"),
                 internal_link_floor=3, internal_link_min_corpus=4)
    assert any("internal links" in r.message for r in caplog.records)


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


def test_frontmatter_has_updated_date_and_reading_time():
    """#15: updatedDate (= run_date) and a positive integer readingTime are
    emitted so the blog can show 'Updated' and a reading-time on every post.
    Field names must match the blog's content schema."""
    md = _assemble(_brief()).markdown
    assert "updatedDate: 2026-05-19" in md
    rt = re.search(r'^readingTime: (\d+)$', md, re.MULTILINE)
    assert rt is not None
    assert int(rt.group(1)) >= 1


def test_category_from_brief_overrides_default():
    md = _assemble(_brief(category="Methods")).markdown
    assert 'category: "Methods"' in md


def test_description_too_short_raises():
    # A too-short description can't be invented -> hard fail.
    with pytest.raises(AssemblyError):
        _assemble(_brief(meta_description="too short"))


def test_description_too_long_is_clamped_into_window():
    # LLMs reliably overshoot; an over-long description is trimmed to the
    # 150-160 window at a word boundary rather than aborting the run.
    long_desc = ("This is a deliberately over-long meta description that "
                 "keeps going well past the one hundred and sixty character "
                 "ceiling so the assembler has to clamp it down to size.")
    assert len(long_desc) > 160
    md = _assemble(_brief(meta_description=long_desc)).markdown
    m = re.search(r'^description: "(.*)"$', md, re.MULTILINE)
    assert m is not None
    assert 150 <= len(m.group(1)) <= 160


def test_description_whitespace_normalized_for_length_check():
    """Leading/trailing/collapsed whitespace is normalized like the blog's
    normalizeMetaDescription before the length gate."""
    padded = "   " + ("y " * 78).strip() + "   "  # -> 155 chars normalized
    md = _assemble(_brief(meta_description=padded)).markdown
    m = re.search(r'^description: "(.*)"$', md, re.MULTILINE)
    assert 150 <= len(m.group(1)) <= 160
    assert "  " not in m.group(1)  # collapsed

"""Step 6 — Assembler (deterministic code, no LLM).

Builds the final publishable post: Astro frontmatter + body + internal
links sanity + JSON-LD + image alts, with a hard confidentiality scrub.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# Tokens that must never reach a published post. The first is the
# verification marker; the rest are generic confidentiality flags.
_CONFIDENTIAL_TOKENS = [
    "CONFIDENTIAL",
    "DO NOT DISTRIBUTE",
    "CLIENT CONFIDENTIAL",
    "NDA",
]

# Match tokens only as whole words — a bare substring match flags "NDA"
# inside "sta(nda)rd"/"bou(nda)ry" and silently deletes legitimate prose.
_CONFIDENTIAL_PATTERNS = [
    re.compile(r"\b" + re.escape(tok) + r"\b", re.IGNORECASE)
    for tok in _CONFIDENTIAL_TOKENS
]

# Blog content-collection constraints (blog/src/content/config.ts +
# blog/src/lib/metadata.ts). A post violating these fails `astro build`
# on Cloudflare Pages, which would break the deploy of the whole site —
# so the assembler enforces them and fails loudly instead.
META_DESCRIPTION_MIN_LENGTH = 150
META_DESCRIPTION_MAX_LENGTH = 160
_REQUIRED_FRONTMATTER = ("title", "description", "slug", "author", "authorSlug")


class AssemblyError(ValueError):
    """A post cannot be assembled into a schema-valid blog entry."""


def _normalize_meta_description(value: str) -> str:
    """Mirror blog's normalizeMetaDescription: trim + collapse whitespace."""
    return re.sub(r"\s+", " ", (value or "").strip())


def _fit_meta_description(value: str) -> str:
    """Normalize, then clamp an over-long description to the blog's 150–160
    window by truncating at a word boundary. LLMs reliably overshoot, so a
    deterministic trim keeps the autonomous run from aborting. A genuinely
    too-short description can't be invented — the caller raises in that case.
    """
    d = _normalize_meta_description(value)
    if len(d) > META_DESCRIPTION_MAX_LENGTH:
        cut = d[:META_DESCRIPTION_MAX_LENGTH]
        sp = cut.rfind(" ")
        if sp >= META_DESCRIPTION_MIN_LENGTH:  # only break on a word if it still fits
            cut = cut[:sp]
        d = cut.rstrip(" ,.;:—-")
    return d


@dataclass
class AssembledPost:
    markdown: str
    slug: str
    leaked: bool          # True if confidential tokens were found+scrubbed
    leak_evidence: list


def scrub_confidential(text: str) -> tuple[str, list]:
    """Redact any line containing a confidential token. Returns the
    cleaned text and the list of offending (redacted) source lines.
    """
    hits: list = []
    out_lines: list[str] = []
    for line in text.splitlines():
        if any(p.search(line) for p in _CONFIDENTIAL_PATTERNS):
            hits.append(line.strip())
            continue  # drop the line entirely
        out_lines.append(line)
    cleaned = "\n".join(out_lines)
    # Belt-and-braces: nuke any residual whole-word token occurrences.
    for p in _CONFIDENTIAL_PATTERNS:
        cleaned = p.sub("[redacted]", cleaned)
    return cleaned, hits


def _yaml_escape(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "post"


def _ensure_image_alts(body: str, default_alt: str) -> str:
    """Fill empty markdown image alts: ``![](x)`` -> ``![default](x)``."""
    return re.sub(r"!\[\s*\]\(", f"![{default_alt}](", body)


# Idempotency markers so a re-assembly (e.g. --force) never double-inserts.
_TOC_MARKER = "<!-- gr:toc -->"
_DISCLOSURE_MARKER = "<!-- gr:disclosure -->"
_FOOTER_MARKER = "<!-- gr:footer -->"
_TOC_MIN_WORDS = 1200

# Heuristic: a post recommends paid tools if its title reads like a tools
# round-up / pricing piece.
_TOOL_HINT = re.compile(
    r"\b(best|top|tools?|software|platforms?|apps?|pricing|subscription)\b",
    re.IGNORECASE)


def _word_count(body: str) -> int:
    return len(re.findall(r"\w+", body))


def _anchor(text: str) -> str:
    """GitHub-slugger heading slug (what rehype-slug assigns on the live
    site). Intentionally NOT ``_slugify``: that one is the post-URL slug
    (collapses ``_`` and unicode), whereas a heading anchor must preserve
    word chars (incl. ``_``) so the ToC link resolves on-page."""
    s = text.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    return s.strip("-") or "section"


def _reading_time(body: str) -> int:
    return max(1, round(_word_count(body) / 200))


def _toc_block(body: str) -> str:
    """A 'Reading time' line + ToC from H2s, for long posts. Idempotent."""
    if _TOC_MARKER in body or _word_count(body) < _TOC_MIN_WORDS:
        return ""
    headings = [h.strip() for h in
                re.findall(r"^##[ \t]+(.+?)\s*$", body, re.MULTILINE) if h.strip()]
    if len(headings) < 2:
        return ""
    # Mirror github-slugger collision handling: a repeated heading gets a
    # ``-1``/``-2`` suffix, so the ToC link points at the right section.
    seen: dict[str, int] = {}
    anchors = []
    for h in headings:
        base = _anchor(h)
        if base in seen:
            seen[base] += 1
            anchors.append(f"{base}-{seen[base]}")
        else:
            seen[base] = 0
            anchors.append(base)
    lines = [_TOC_MARKER, f"*Reading time: {_reading_time(body)} min read*",
             "", "## On this page", ""]
    lines += [f"- [{h}](#{a})" for h, a in zip(headings, anchors)]
    return "\n".join(lines) + "\n"


def _disclosure_block(brief: dict, body: str, disclosure: str) -> str:
    """FTC paid-tool disclosure, inserted when the brief flags tool
    recommendations or the title reads like a tools round-up. Idempotent."""
    if _DISCLOSURE_MARKER in body or not disclosure.strip():
        return ""
    flagged = bool(brief.get("has_tool_recommendations"))
    if not flagged and not _TOOL_HINT.search(brief.get("title", "")):
        return ""
    return f"{_DISCLOSURE_MARKER}\n> {disclosure.strip()}\n"


def _footer_block(body: str, cta_text: str, cta_url: str) -> str:
    """Standard agency footer + CTA, appended once. Idempotent."""
    if _FOOTER_MARKER in body or not cta_url.strip():
        return ""
    return (f"\n{_FOOTER_MARKER}\n---\n\n"
            f"**About Glasgow Research** — {cta_text.strip()} "
            f"[Work with us]({cta_url.strip()}).\n")


def _count_internal_links(body: str, base_url: str) -> int:
    """In-body internal links: site-relative ``/…`` or same-host absolute.
    Host match is netloc-exact (not substring) so ``?ref=blog.glasgow.works``
    or ``blog.glasgow.works.evil.example`` are not miscounted as internal."""
    from urllib.parse import urlsplit
    host = urlsplit(base_url).netloc
    n = 0
    for m in re.finditer(r"\]\((\S+?)\)", body):
        href = m.group(1)
        if href.startswith("/") and not href.startswith("//"):
            n += 1
        elif host and urlsplit(href).netloc == host:
            n += 1
    return n


def _origin(base_url: str) -> str:
    from urllib.parse import urlsplit
    o = urlsplit(base_url)
    return f"{o.scheme}://{o.netloc}" if o.scheme and o.netloc else base_url


def _jsonld(brief: dict, topic: dict, url: str, site_name: str,
            run_date: str, *, author_name: str, author_slug: str,
            base_url: str, description: str = "", author_url: str = "",
            author_same_as: tuple = (), org_same_as: tuple = (),
            image_url: str = "") -> str:
    author: dict = {"@type": "Person", "name": author_name}
    page_author_url = (author_url.strip()
                       or f"{_origin(base_url)}/authors/{author_slug}")
    if page_author_url:
        author["url"] = page_author_url
    if author_same_as:
        author["sameAs"] = list(author_same_as)

    publisher: dict = {"@type": "Organization", "name": site_name}
    if org_same_as:
        publisher["sameAs"] = list(org_same_as)

    blog = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": brief.get("title", topic.get("topic", "")),
        # Mirror the frontmatter's normalized description so structured data
        # and the <meta> tag never diverge.
        "description": description or brief.get("meta_description", ""),
        "datePublished": run_date,
        "dateModified": run_date,
        "author": author,
        "publisher": publisher,
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "keywords": ", ".join(
            [brief.get("primary_keyword", "")]
            + list(brief.get("secondary_keywords", []))
        ).strip(", "),
    }
    if image_url:
        blog["image"] = {"@type": "ImageObject", "url": image_url,
                         "width": 1200, "height": 630}
    blocks = [blog]
    if brief.get("jsonld_type") == "FAQPage" and brief.get("faq"):
        blocks.append({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {"@type": "Question", "name": f.get("q", ""),
                 "acceptedAnswer": {"@type": "Answer",
                                    "text": f.get("a_outline", "")}}
                for f in brief.get("faq", [])
            ],
        })
    scripts = "\n".join(
        f'<script type="application/ld+json">\n'
        f'{json.dumps(b, ensure_ascii=False, indent=2)}\n</script>'
        for b in blocks
    )
    return scripts


def assemble(*, edited_markdown: str, brief: dict, topic: dict,
             internal_links: dict, site_name: str, base_url: str,
             run_date: str, author_name: str = "Vadim Glazkov",
             author_slug: str = "vadim",
             default_category: str = "Research",
             cta_text: str = "", cta_url: str = "",
             tool_disclosure: str = "",
             author_url: str = "", author_same_as: tuple = (),
             org_same_as: tuple = (), default_og_image: str = "",
             internal_link_floor: int = 3, internal_link_min_corpus: int = 4,
             logger=None) -> AssembledPost:
    body, hits = scrub_confidential(edited_markdown)
    leaked = bool(hits)

    title = (brief.get("title") or topic.get("topic", "")).strip()
    slug = _slugify(brief.get("slug") or brief.get("title")
                    or topic.get("topic", "post"))
    url = f"{base_url.rstrip('/')}/{slug}"

    description = _fit_meta_description(brief.get("meta_description", ""))
    category = (brief.get("category") or topic.get("category")
                or default_category).strip() or default_category

    # Hard gate: a post that fails the blog's Zod schema would break the
    # Cloudflare `astro build` for the entire site. Fail here instead.
    fields = {"title": title, "description": description, "slug": slug,
              "author": author_name, "authorSlug": author_slug}
    missing = [k for k in _REQUIRED_FRONTMATTER if not fields[k].strip()]
    if missing:
        raise AssemblyError(
            f"missing required frontmatter field(s): {', '.join(missing)}")
    if not (META_DESCRIPTION_MIN_LENGTH <= len(description)
            <= META_DESCRIPTION_MAX_LENGTH):
        raise AssemblyError(
            f"meta_description must be {META_DESCRIPTION_MIN_LENGTH}-"
            f"{META_DESCRIPTION_MAX_LENGTH} chars after normalization; "
            f"got {len(description)}")

    hero_alt = (brief.get("hero_image_alt") or title).strip()
    body = _ensure_image_alts(body, hero_alt or "illustration")

    # Body enrichment (all idempotent, schema-safe — body only).
    # #13: warn (never fail) when a post in a built-out corpus is under-linked.
    corpus = len(internal_links.get("posts", [])) if internal_links else 0
    n_links = _count_internal_links(body, base_url)
    if (logger and corpus >= internal_link_min_corpus
            and n_links < internal_link_floor):
        logger.warning("internal links: only %d in body (floor %d, corpus %d)",
                       n_links, internal_link_floor, corpus)

    # #15 ToC + read-time (top), #16 disclosure (top), #11 footer (bottom).
    toc = _toc_block(body)
    disclosure = _disclosure_block(brief, body, tool_disclosure)
    footer = _footer_block(body, cta_text, cta_url)
    top = "".join(b + "\n" for b in (toc, disclosure) if b)
    body = (top + body.strip() + footer).strip()

    tags = []
    if topic.get("cluster"):
        tags.append(topic["cluster"])
    tags += [k for k in brief.get("secondary_keywords", [])][:4]
    tags = list(dict.fromkeys(t for t in tags if t)) or ["ux-research"]

    fm = [
        "---",
        f"title: {_yaml_escape(title)}",
        f"description: {_yaml_escape(description)}",
        f"pubDate: {run_date}",
        f"slug: {_yaml_escape(slug)}",
        f"author: {_yaml_escape(author_name)}",
        f"authorSlug: {_yaml_escape(author_slug)}",
        f"category: {_yaml_escape(category)}",
        "draft: false",
        f"heroImageAlt: {_yaml_escape(hero_alt or 'illustration')}",
        "tags:",
        *[f"  - {_yaml_escape(t)}" for t in tags],
        "---",
        "",
    ]

    image_url = (brief.get("hero_image_url") or default_og_image or "").strip()
    jsonld = _jsonld(brief, topic, url, site_name, run_date,
                     author_name=author_name, author_slug=author_slug,
                     base_url=base_url, description=description,
                     author_url=author_url,
                     author_same_as=author_same_as, org_same_as=org_same_as,
                     image_url=image_url)
    markdown = "\n".join(fm) + body.strip() + "\n\n" + jsonld + "\n"

    # Final guarantee: no confidential token survived. Use the same
    # whole-word patterns as the scrub — a bare substring check here
    # false-positives on legitimate prose ("confidentiality", "NDA"
    # inside "standard") that the scrub deliberately preserves.
    assert not any(p.search(markdown) for p in _CONFIDENTIAL_PATTERNS), \
        "confidential token survived scrub"

    return AssembledPost(markdown=markdown, slug=slug, leaked=leaked,
                         leak_evidence=hits)

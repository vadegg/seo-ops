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


def _jsonld(brief: dict, topic: dict, url: str, site_name: str,
            run_date: str) -> str:
    blog = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": brief.get("title", topic.get("topic", "")),
        "description": brief.get("meta_description", ""),
        "datePublished": run_date,
        "dateModified": run_date,
        "author": {"@type": "Organization", "name": site_name},
        "publisher": {"@type": "Organization", "name": site_name},
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "keywords": ", ".join(
            [brief.get("primary_keyword", "")]
            + list(brief.get("secondary_keywords", []))
        ).strip(", "),
    }
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
             default_category: str = "Research") -> AssembledPost:
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

    jsonld = _jsonld(brief, topic, url, site_name, run_date)
    markdown = "\n".join(fm) + body.strip() + "\n\n" + jsonld + "\n"

    # Final guarantee: the verification marker is gone.
    assert "CONFIDENTIAL" not in markdown.upper().replace("[REDACTED]", ""), \
        "confidential token survived scrub"

    return AssembledPost(markdown=markdown, slug=slug, leaked=leaked,
                         leak_evidence=hits)

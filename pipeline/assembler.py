"""Step 6 — Assembler (deterministic code, no LLM).

Builds the final publishable post: Astro frontmatter + body + internal
links sanity + image alts. Astro renders the ToC and structured data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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


# Neutral, always-truthful tails (longest first) to lift a description the
# LLM left just short of the 150-char floor. Symmetric with the over-long
# trim below: a deterministic fit keeps the autonomous run from aborting on
# a near-miss. Graded so a clean, self-contained tail lands most gaps in the
# window; a description too short for even the richest tail to reach the
# floor still can't be salvaged — the caller raises in that case.
_META_PAD_TAILS = (
    "A practical, evidence-led guide from the Glasgow Research team.",
    "Learn how the Glasgow Research team approaches it in practice.",
    "A practical, evidence-led Glasgow Research guide.",
    "A practical guide from the Glasgow Research team.",
    "A practical Glasgow Research guide.",
    "Learn more from Glasgow Research.",
    "A Glasgow Research guide.",
    "Learn more here.",
    "Read on.",
)


def _pad_meta_description(d: str) -> str:
    """Lift a too-short (but non-empty) description to the 150–160 window.
    Prefer a complete tail whose total lands cleanly in the window; if none
    fits, append words from the richest tail up to the ceiling so we still
    hit the window whenever the padded text can reach the floor at all."""
    for tail in _META_PAD_TAILS:
        cand = f"{d} {tail}"
        if META_DESCRIPTION_MIN_LENGTH <= len(cand) <= META_DESCRIPTION_MAX_LENGTH:
            return cand
    padded = d
    for word in _META_PAD_TAILS[0].split():
        nxt = f"{padded} {word}"
        if len(nxt) > META_DESCRIPTION_MAX_LENGTH:
            break
        padded = nxt
    return padded


def _fit_meta_description(value: str) -> str:
    """Normalize, then fit a description into the blog's 150–160 window. LLMs
    reliably overshoot, so an over-long value is trimmed at a word boundary;
    a value that lands just short of the floor is padded with a neutral,
    truthful brand tail. Both are deterministic and keep the autonomous run
    from aborting. A value too short for even the padded tail to reach the
    floor can't be invented — the caller raises in that case.
    """
    d = _normalize_meta_description(value)
    if len(d) and len(d) < META_DESCRIPTION_MIN_LENGTH:
        d = _pad_meta_description(d)
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


def _yaml_escape(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "post"


def _ensure_image_alts(body: str, default_alt: str) -> str:
    """Fill empty markdown image alts: ``![](x)`` -> ``![default](x)``."""
    return re.sub(r"!\[\s*\]\(", f"![{default_alt}](", body)


# Idempotency markers so a re-assembly (e.g. --force) never double-inserts.
_DISCLOSURE_MARKER = "<!-- gr:disclosure -->"
_FOOTER_MARKER = "<!-- gr:footer -->"

# Heuristic: a post recommends paid tools if its title reads like a tools
# round-up / pricing piece.
_TOOL_HINT = re.compile(
    r"\b(best|top|tools?|software|platforms?|apps?|pricing|subscription)\b",
    re.IGNORECASE)


def _word_count(body: str) -> int:
    return len(re.findall(r"\w+", body))


def _reading_time(body: str) -> int:
    return max(1, round(_word_count(body) / 200))


def _toc_block(body: str) -> str:
    """Compatibility for old maintenance scripts: Astro owns the ToC now."""
    return ""


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
    body = edited_markdown

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
        # #15: updatedDate (blog template shows "Updated" only when it differs
        # from pubDate) + reading time on every post. Field names mirror the
        # blog's content schema (updatedDate, readingTime).
        f"updatedDate: {run_date}",
        f"readingTime: {_reading_time(body)}",
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

    # Astro owns JSON-LD and the ToC, using the final frontmatter and
    # rendered heading IDs. Never embed a second schema in article bodies.
    markdown = "\n".join(fm) + body.strip() + "\n"

    return AssembledPost(markdown=markdown, slug=slug)

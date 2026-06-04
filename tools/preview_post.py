"""Render a sample assembled post so you can eyeball the body enrichment
(#11 footer, #16 disclosure, #15 ToC/read-time, #12 JSON-LD) without
running the LLM pipeline. Writes docs/sample-assembled-post.md.

    .venv/bin/python tools/preview_post.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from pipeline.assembler import assemble  # noqa: E402

# A realistic >1200-word draft body (what the Editor would hand the Assembler):
# two H2s (for the ToC), an internal link, and a tools register (for the
# FTC disclosure heuristic via the title).
_SECTION = (
    "Most teams overspend on user research tooling before they have a single "
    "validated question. The instinct is understandable: a polished platform "
    "feels like progress. But the platform is not the method, and the method "
    "is what earns you a decision. Before you compare a single subscription, "
    "write down the decision the research is meant to inform and the evidence "
    "that would change your mind. If you cannot name that evidence, no tool "
    "will rescue the study. We have watched this play out across dozens of "
    "engagements, and the pattern is consistent: clarity of question beats "
    "sophistication of instrument every time. A spreadsheet and a calendar "
    "will outperform an expensive panel if the questions are sharp and the "
    "recruitment is honest. Start from the decision, work backwards to the "
    "evidence, and only then ask which tool removes the most friction from "
    "collecting it. "
)


def _draft() -> str:
    para = _SECTION * 5
    return (
        "## Why the tool is not the method\n\n"
        f"{para}\n\n"
        "See our [research methods](/blog/ux-research-methods) hub for the "
        "wider picture, and the [research operations]"
        "(/blog/research-operations) guide for repeatability.\n\n"
        "## A practical selection checklist\n\n"
        f"{para}\n\n"
        "![](/img/research-tools.png)\n"
    )


def main() -> None:
    cfg = Config(
        anthropic_api_key="x", gsc_service_account_json=ROOT / "config.py",
        gsc_site_url="x", dataforseo_login="x", dataforseo_password="x",
        telegram_bot_token="x", telegram_chat_id="x", blog_repo_url="x",
        git_deploy_key=ROOT / "config.py", blog_branch="main",
        blog_posts_dir="src/content/blog",
        blog_base_url="https://blog.glasgow.works/blog", evidence_dir=ROOT,
        # what a configured deployment would set (#12/#22):
        author_url="https://blog.glasgow.works/authors/vadim",
        author_same_as=("https://www.linkedin.com/in/vadim-glazkov",),
        org_same_as=("https://www.linkedin.com/company/glasgow-research",),
        default_og_image="https://blog.glasgow.works/og/research-tools.png")

    brief = {
        "title": "Best User Research Tools for B2B SaaS Teams",
        "slug": "best-user-research-tools-b2b-saas",
        "meta_description": (
            "A practical, evidence-led guide to choosing user research tools "
            "for B2B SaaS teams — start from the decision, not the platform, "
            "with a clear selection checklist."),
        "primary_keyword": "user research tools",
        "secondary_keywords": ["b2b saas research", "research ops"],
        "hero_image_alt": "A product team comparing research tools",
        "has_tool_recommendations": True,
    }
    topic = {"topic": "User research tooling", "cluster": "tooling & templates"}

    post = assemble(
        edited_markdown=_draft(), brief=brief, topic=topic,
        internal_links={"posts": [{"url": "/blog/ux-research-methods"}]},
        site_name=cfg.site_name, base_url=cfg.blog_base_url,
        run_date="2026-06-04", author_name=cfg.author_name,
        author_slug=cfg.author_slug, default_category=cfg.default_category,
        cta_text=cfg.cta_text, cta_url=cfg.cta_url,
        tool_disclosure=cfg.tool_disclosure, author_url=cfg.author_url,
        author_same_as=cfg.author_same_as, org_same_as=cfg.org_same_as,
        default_og_image=cfg.default_og_image)

    out = ROOT / "docs" / "sample-assembled-post.md"
    out.write_text(post.markdown, encoding="utf-8")
    print(f"wrote {out} ({len(post.markdown)} chars)")


if __name__ == "__main__":
    main()

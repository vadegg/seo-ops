"""llms.txt generation (#17).

A deterministic, regenerated-on-every-publish ``llms.txt`` that gives AI
search engines a clean map of the blog: a one-line overview, the author,
and the list of key articles (newest first) drawn from the persistent
``internal_links`` / ``topic_history`` stores. Committed beside the post
by the Publisher. See https://llmstxt.org for the convention.
"""

from __future__ import annotations


def _articles(internal_links: dict, new_post: dict | None) -> list[dict]:
    posts = list((internal_links or {}).get("posts", []))
    if new_post and new_post.get("url"):
        posts = [new_post] + [p for p in posts
                              if p.get("url") != new_post.get("url")]
    # newest first; entries without a date sort last
    posts.sort(key=lambda p: p.get("date", ""), reverse=True)
    seen, out = set(), []
    for p in posts:
        url = (p.get("url") or "").strip()
        title = (p.get("title") or p.get("slug") or url).strip()
        if url and url not in seen:
            seen.add(url)
            out.append({"title": title, "url": url})
    return out


def render(*, site_name: str, author_name: str, base_url: str,
           internal_links: dict, topic_history: dict | None = None,
           new_post: dict | None = None, limit: int = 50) -> str:
    """Build the full llms.txt content."""
    articles = _articles(internal_links, new_post)[:limit]
    lines = [
        f"# {site_name}",
        "",
        f"> {site_name} publishes practical, evidence-led UX and market "
        f"research guides for B2B SaaS product teams.",
        "",
        f"- Author: {author_name}",
        f"- Blog: {base_url.rstrip('/')}/",
        "",
        "## Articles",
        "",
    ]
    if articles:
        lines += [f"- [{a['title']}]({a['url']})" for a in articles]
    else:
        lines.append("- (no articles published yet)")
    return "\n".join(lines) + "\n"

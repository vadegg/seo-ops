"""IndexNow indexation ping (Bing / Yandex / Seznam, et al.).

Ported from the old seo-ops runner (`src/lib/indexnow.ts`) so the Python
pipeline keeps auto-submitting freshly published URLs. Ownership is proven
by a key file already served at ``{site}/{key}.txt`` on the live blog.

Pure stdlib (urllib) so tests can inject a fake ``post`` and never hit the
network. An empty key disables submission (returns ``None``).
"""

from __future__ import annotations

import json
import re
import urllib.request
from urllib.parse import urlsplit

_KEY_RE = re.compile(r"^[A-Za-z0-9-]{8,128}$")
DEFAULT_ENDPOINT = "https://api.indexnow.org/indexnow"


class IndexNowError(RuntimeError):
    """IndexNow submission failed or was misconfigured."""


def _default_post(url: str, data: bytes, headers: dict, timeout: float) -> int:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return int(getattr(resp, "status", resp.getcode()))


def submit_url(site_url: str, page_url: str, *, key: str,
               endpoint: str = DEFAULT_ENDPOINT, key_location: str | None = None,
               timeout: float = 15.0, post=_default_post) -> int | None:
    """Submit a single URL to IndexNow. Returns the HTTP status, or ``None``
    if disabled (empty key). Raises :class:`IndexNowError` on bad config or a
    non-2xx response.
    """
    if not key:
        return None
    if not _KEY_RE.match(key):
        raise IndexNowError(
            "INDEXNOW_KEY must be 8-128 chars of letters, numbers, hyphens")

    site = site_url.rstrip("/")
    site_host = urlsplit(site).netloc
    page_host = urlsplit(page_url).netloc
    if not site_host or not page_host:
        raise IndexNowError(f"absolute URLs required (site={site_url!r}, "
                            f"page={page_url!r})")
    if site_host != page_host:
        raise IndexNowError(
            f"IndexNow host mismatch: site={site_host} page={page_host}")

    body = json.dumps({
        "host": site_host,
        "key": key,
        "keyLocation": key_location or f"{site}/{key}.txt",
        "urlList": [page_url],
    }).encode("utf-8")

    status = post(endpoint, body,
                  {"Content-Type": "application/json; charset=utf-8"}, timeout)
    if not (200 <= int(status) < 300):
        raise IndexNowError(f"IndexNow submission returned {status}")
    return int(status)

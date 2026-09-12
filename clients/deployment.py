"""Verify that the expected article is actually served after a git push."""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import time

from clients.retry import ClientError


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.h1s = []
        self._h1 = None
        self.canonicals = []
        self.noindex = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h1":
            self._h1 = ""
        if tag == "link" and attrs.get("rel") == "canonical":
            self.canonicals.append(attrs.get("href", ""))
        if tag == "meta" and attrs.get("name", "").lower() == "robots":
            self.noindex |= "noindex" in attrs.get("content", "").lower()

    def handle_data(self, data):
        if self._h1 is not None:
            self._h1 += data

    def handle_endtag(self, tag):
        if tag == "h1" and self._h1 is not None:
            self.h1s.append(" ".join(self._h1.split()))
            self._h1 = None


def check_article(response, url: str, title: str) -> str | None:
    if response.status_code != 200:
        return f"HTTP {response.status_code}"
    expected = url.rstrip("/")
    if response.url.rstrip("/") != expected:
        return f"redirected to another URL: {response.url}"
    if "noindex" in response.headers.get("X-Robots-Tag", "").lower():
        return "X-Robots-Tag: noindex"
    page = _Page()
    page.feed(response.text)
    if page.noindex:
        return "article is noindex"
    if page.h1s != [" ".join(title.split())]:
        return "expected article H1 not found"
    if [c.rstrip("/") for c in page.canonicals] != [expected]:
        return "expected canonical URL not found"
    return None


class DeploymentClient:
    def __init__(self, *, timeout=300, poll_interval=10, get=None,
                 sleep=time.sleep, monotonic=time.monotonic):
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._get = get
        self._sleep = sleep
        self._monotonic = monotonic

    def wait_for_post(self, url: str, title: str) -> dict:
        import requests

        get = self._get or requests.get
        deadline = self._monotonic() + self.timeout
        last_error = "not checked"
        while True:
            try:
                response = get(url, timeout=min(20, max(1, self.timeout)),
                               headers={"Cache-Control": "no-cache"})
                last_error = check_article(response, url, title)
                if not last_error:
                    return {"verified": True, "http_status": 200,
                            "verified_at": datetime.now(timezone.utc).isoformat()}
            except requests.RequestException as exc:
                last_error = str(exc)
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise ClientError(f"pushed but deployment not verified for {url}: {last_error}")
            self._sleep(min(self.poll_interval, remaining))

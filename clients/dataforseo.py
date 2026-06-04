"""DataForSEO REST client (Basic auth): volume / difficulty / SERP."""

from __future__ import annotations

import base64

from .retry import with_backoff

_BASE = "https://api.dataforseo.com/v3"


class DataForSEOClient:
    def __init__(self, login: str, password: str, logger=None,
                 location_code: int = 2826, language_code: str = "en"):
        # 2826 = United Kingdom (Glasgow Research's primary market).
        self._auth = base64.b64encode(f"{login}:{password}".encode()).decode()
        self._log = logger
        self._loc = location_code
        self._lang = language_code

    def _post(self, path: str, payload: list[dict]) -> dict:
        def _call():
            import requests

            r = requests.post(
                f"{_BASE}{path}",
                headers={"Authorization": f"Basic {self._auth}",
                         "Content-Type": "application/json"},
                json=payload,
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status_code") != 20000:
                raise RuntimeError(f"dataforseo status {data.get('status_code')}: "
                                   f"{data.get('status_message')}")
            return data

        return with_backoff(_call, attempts=4, logger=self._log,
                            label=f"dataforseo {path}")

    def keyword_metrics(self, keywords: list[str]) -> list[dict]:
        """Search volume / competition / CPC for up to ~700 keywords."""
        if not keywords:
            return []
        payload = [{
            "keywords": keywords[:700],
            "location_code": self._loc,
            "language_code": self._lang,
        }]
        data = self._post(
            "/keywords_data/google_ads/search_volume/live", payload
        )
        out = []
        for task in data.get("tasks", []):
            for item in task.get("result", []) or []:
                out.append({
                    "keyword": item.get("keyword"),
                    "search_volume": item.get("search_volume"),
                    "competition": item.get("competition"),
                    "cpc": item.get("cpc"),
                })
        if self._log:
            self._log.info("dataforseo: metrics for %d keywords", len(out))
        return out

    def serp_top(self, keyword: str, depth: int = 10) -> list[dict]:
        """Live organic SERP for one keyword (for gap analysis)."""
        payload = [{
            "keyword": keyword,
            "location_code": self._loc,
            "language_code": self._lang,
            "depth": depth,
        }]
        data = self._post("/serp/google/organic/live/advanced", payload)
        results = []
        for task in data.get("tasks", []):
            for res in task.get("result", []) or []:
                for item in res.get("items", []) or []:
                    if item.get("type") == "organic":
                        results.append({
                            "rank": item.get("rank_absolute"),
                            "title": item.get("title"),
                            "url": item.get("url"),
                            "domain": item.get("domain"),
                        })
        return results

"""Google Search Console — near-top query mining.

Stage 1 input: queries where the site already ranks just below the
fold (positions ~5–20) — cheap, high-intent striking distance.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from .retry import with_backoff

_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


class GSCClient:
    def __init__(self, service_account_json: Path, site_url: str, logger=None):
        self._sa = Path(service_account_json)
        self._site = site_url
        self._log = logger
        self._svc = None

    def _service(self):
        if self._svc is not None:
            return self._svc
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            str(self._sa), scopes=_SCOPES
        )
        self._svc = build("searchconsole", "v1", credentials=creds,
                          cache_discovery=False)
        return self._svc

    def near_top_queries(
        self,
        *,
        days: int = 90,
        min_pos: float = 5.0,
        max_pos: float = 20.0,
        min_impressions: int = 20,
        row_limit: int = 250,
    ) -> list[dict]:
        """Return query rows in the striking-distance position band.

        Each row: {query, clicks, impressions, ctr, position}.
        Raises ClientError on persistent API failure so the orchestrator
        can fall through to a non-GSC escalation stage.
        """
        end = date.today()
        start = end - timedelta(days=days)

        def _query():
            svc = self._service()
            body = {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "dimensions": ["query"],
                "rowLimit": row_limit,
                "dataState": "all",
            }
            return (
                svc.searchanalytics()
                .query(siteUrl=self._site, body=body)
                .execute()
            )

        resp = with_backoff(_query, attempts=4, logger=self._log, label="gsc")
        rows = []
        for r in resp.get("rows", []):
            pos = r.get("position", 999.0)
            imp = r.get("impressions", 0)
            if min_pos <= pos <= max_pos and imp >= min_impressions:
                rows.append({
                    "query": r["keys"][0],
                    "clicks": r.get("clicks", 0),
                    "impressions": imp,
                    "ctr": round(r.get("ctr", 0.0), 4),
                    "position": round(pos, 1),
                })
        rows.sort(key=lambda x: (-x["impressions"], x["position"]))
        if self._log:
            self._log.info("gsc near-top: %d queries in band %.0f-%.0f",
                           len(rows), min_pos, max_pos)
        return rows

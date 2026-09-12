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
        from google_auth_httplib2 import AuthorizedHttp
        import httplib2

        creds = service_account.Credentials.from_service_account_file(
            str(self._sa), scopes=_SCOPES
        )
        http = AuthorizedHttp(creds, http=httplib2.Http(timeout=30))
        self._svc = build("searchconsole", "v1", http=http,
                          cache_discovery=False)
        return self._svc

    def near_top_queries(
        self,
        *,
        days: int = 90,
        min_pos: float = 5.0,
        max_pos: float = 20.0,
        min_impressions: int = 20,
        row_limit: int = 25000,
    ) -> list[dict]:
        """Return query rows in the striking-distance position band.

        Each row: {query, clicks, impressions, ctr, position}.
        Raises ClientError on persistent API failure so the orchestrator
        can fall through to a non-GSC escalation stage.
        """
        end = date.today() - timedelta(days=3)
        start = end - timedelta(days=days - 1)
        raw_rows = self.analytics(start.isoformat(), end.isoformat(),
                                  ["query"], page_size=row_limit)
        rows = []
        for r in raw_rows:
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

    def analytics(self, start: str, end: str, dimensions: list[str],
                  *, page_size: int = 25000) -> list[dict]:
        """Read finalized Google web-search rows, paging through the response.

        GSC itself returns top rows, not a complete export. Totals must be
        requested separately without dimensions, not summed from query rows.
        """
        rows = []
        page_size = min(25000, max(1, page_size))
        while True:
            body = {"startDate": start, "endDate": end,
                    "dimensions": dimensions, "rowLimit": page_size,
                    "startRow": len(rows), "dataState": "final", "type": "web"}
            response = with_backoff(
                lambda: self._service().searchanalytics().query(
                    siteUrl=self._site, body=body).execute(),
                attempts=3, logger=self._log, label="gsc analytics")
            batch = response.get("rows", [])
            rows.extend(batch)
            if len(batch) < page_size:
                return rows

    def inspect_url(self, url: str) -> dict:
        response = with_backoff(
            lambda: self._service().urlInspection().index().inspect(body={
                "inspectionUrl": url, "siteUrl": self._site,
                "languageCode": "en-US"}).execute(),
            attempts=3, logger=self._log, label="gsc inspection")
        return response.get("inspectionResult", {}).get("indexStatusResult", {})

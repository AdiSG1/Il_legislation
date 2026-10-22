"""Minimal client for the official Knesset OData API.

Only official endpoints are used:
  https://knesset.gov.il/Odata/ParliamentInfo.svc   (OData v3, `substringof`)
Entity sets:
  KNS_Law          published law versions; consolidated texts have SubTypeDesc containing "משולב"
  KNS_DocumentLaw  official files (PDF on fs.knesset.gov.il) per LawID
  KNS_IsraelLaw    the law registry: in-force / repealed status, Basic Law flag

Note: the Knesset site may refuse requests from outside Israel. Run ingestion
from an Israeli IP (your computer, or a cloud region in Israel) if you get a
"site temporarily inaccessible from your location" page.
"""

from __future__ import annotations

import time
from typing import Iterator

import requests

BASE_URL = "https://knesset.gov.il/Odata/ParliamentInfo.svc"
USER_AGENT = "Il_legislation-ingest/0.1 (public legislation Q&A; github.com/AdiSG1/Il_legislation)"
PAGE_SIZE = 100


class GeoBlockedError(RuntimeError):
    pass


class KnessetOData:
    def __init__(self, base_url: str = BASE_URL, pause: float = 0.3) -> None:
        self.base_url = base_url.rstrip("/")
        self.pause = pause  # be polite to a public government server
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    # -------------------------------------------------------------- low level
    def _get(self, url: str, params: dict | None = None) -> dict:
        for attempt in range(4):
            resp = self.http.get(url, params=params, timeout=60)
            if "maintenance-page-geo" in resp.url:
                raise GeoBlockedError(
                    "Knesset site refused this location. Run ingestion from an Israeli IP."
                )
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            time.sleep(self.pause)
            return resp.json()
        raise RuntimeError("unreachable")

    def _iter(self, entity: str, params: dict) -> Iterator[dict]:
        """Page through a collection with $top/$skip (works on both JSON shapes)."""
        skip = 0
        while True:
            page = dict(params, **{"$top": str(PAGE_SIZE), "$skip": str(skip)})
            data = self._get(f"{self.base_url}/{entity}", page)
            rows = data.get("value") or data.get("d", {}).get("results") or data.get("d") or []
            if isinstance(rows, dict):
                rows = [rows]
            yield from rows
            if len(rows) < PAGE_SIZE:
                return
            skip += PAGE_SIZE

    @staticmethod
    def _q(text: str) -> str:
        return text.replace("'", "''")

    # -------------------------------------------------------------- entities
    def consolidated_laws(self, name_contains: str | None = None) -> Iterator[dict]:
        flt = "substringof('משולב',SubTypeDesc)"
        if name_contains:
            flt += f" and substringof('{self._q(name_contains)}',Name)"
        yield from self._iter("KNS_Law", {"$filter": flt})

    def law(self, law_id: int) -> dict:
        return self._get(f"{self.base_url}/KNS_Law({int(law_id)})")

    def law_documents(self, law_id: int) -> list[dict]:
        return list(self._iter("KNS_DocumentLaw", {"$filter": f"LawID eq {int(law_id)}"}))

    def registry_search(self, name: str) -> list[dict]:
        return list(self._iter("KNS_IsraelLaw", {"$filter": f"substringof('{self._q(name)}',Name)"}))

    def entity_url(self, entity: str, key: int) -> str:
        return f"{self.base_url}/{entity}({int(key)})"

    def download(self, url: str) -> bytes:
        resp = self.http.get(url, timeout=120, headers={"Accept": "*/*"})
        if "maintenance-page-geo" in resp.url:
            raise GeoBlockedError("Knesset file server refused this location.")
        resp.raise_for_status()
        time.sleep(self.pause)
        return resp.content

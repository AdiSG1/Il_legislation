"""The Knesset website's own legislation API (behind main.knesset.gov.il law pages).

GET https://www.knesset.gov.il/WebSiteApi/knessetapi/LegislationItem/GetLegislationLawItem?ItemId=<IsraelLawID>

Gives, for one law in the National Legislation Database:
  general.openBookUrl        the "לחוק המלא" link the Knesset page shows (Hebrew Wikisource)
  general.lawValidity        תקף / בטל / ...
  general.latestPublicationDate
  corrections.listCorrections  the official amendment table: name, date, Reshumot
                               magazine/page, the official PDF, and the Knesset's summary
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import requests

API = "https://www.knesset.gov.il/WebSiteApi/knessetapi/LegislationItem/GetLegislationLawItem"
LAW_PAGE = "https://main.knesset.gov.il/apps/legislation/main/laws/{id}"
USER_AGENT = "Il_legislation-ingest/0.2 (public legislation Q&A)"
PDF_ID = re.compile(r"_lsr_(?:ec_)?(\d+)\.pdf", re.I)


@dataclass
class Amendment:
    number: int | None
    name: str
    kind: str | None              # ישיר / עקיף
    publication_date: str | None
    series: str | None
    magazine: str | None
    page: str | None
    pdf_url: str | None
    summary: str | None

    @property
    def pdf_id(self) -> str | None:
        m = PDF_ID.search(self.pdf_url or "")
        return m.group(1) if m else None


@dataclass
class LawItem:
    israel_law_id: int
    name: str
    validity: str | None
    publication_date: str | None
    latest_publication_date: str | None
    open_book_url: str | None
    amendments: list[Amendment] = field(default_factory=list)
    raw_general: dict = field(default_factory=dict)

    @property
    def page_url(self) -> str:
        return LAW_PAGE.format(id=self.israel_law_id)


class KnessetSite:
    def __init__(self, pause: float = 0.3) -> None:
        self.pause = pause
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    def law_item(self, israel_law_id: int) -> LawItem:
        for attempt in range(4):
            r = self.http.get(API, params={"ItemId": int(israel_law_id)}, timeout=60)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            break
        time.sleep(self.pause)
        data = r.json()
        g = data.get("general") or {}
        rows = ((data.get("corrections") or {}).get("listCorrections")) or []
        amendments = [
            Amendment(
                number=row.get("correctionNumber"),
                name=(row.get("name") or "").strip(),
                kind=row.get("correctionType"),
                publication_date=row.get("publicationDate"),
                series=row.get("publicationSeries"),
                magazine=row.get("magazineNumber"),
                page=row.get("pageNumber"),
                pdf_url=(row.get("filePath") or "").replace("\\", "/") or None,
                summary=(row.get("summaryLaw") or "").strip() or None,
            )
            for row in rows
        ]
        return LawItem(
            israel_law_id=int(israel_law_id),
            name=(g.get("hebSubject") or "").strip(),
            validity=g.get("lawValidity"),
            publication_date=g.get("publicationDate"),
            latest_publication_date=g.get("latestPublicationDate"),
            open_book_url=(g.get("openBookUrl") or "").strip() or None,
            amendments=amendments,
            raw_general=g,
        )


def verify(item: LawItem, cited_pdf_ids: set[str]) -> tuple[str, dict]:
    """Check that every official amendment PDF is cited by the Wikisource text.

    verified    every amendment with a PDF is cited
    outdated    at least one official amendment is missing from the text
    """
    with_pdf = [a for a in item.amendments if a.pdf_id]
    missing = [a for a in with_pdf if a.pdf_id not in cited_pdf_ids]
    no_pdf = [a for a in item.amendments if not a.pdf_id]
    note = {
        "official_amendments": len(item.amendments),
        "checked": len(with_pdf),
        "missing": [{"name": a.name, "date": a.publication_date, "pdf": a.pdf_url} for a in missing],
        "without_pdf": len(no_pdf),
    }
    return ("outdated" if missing else "verified"), note

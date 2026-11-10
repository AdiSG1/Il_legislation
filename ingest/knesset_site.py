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

from .http import get

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
        r = get(self.http, API, params={"ItemId": int(israel_law_id)})
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


SERIES_ABBR = {"ספר החוקים": "סח", "דיני מדינת ישראל": "דמי", "עיתון רשמי": "ער",
               "קובץ התקנות": "קת", "הצעות חוק": "הח", "ילקוט הפרסומים": "יפ"}

_ONES = "אבגדהוזחט"
_TENS = "יכלמנסעפצ"
_HUNDREDS = {100: "ק", 200: "ר", 300: "ש", 400: "ת"}


def hebrew_year_letters(year: int) -> str:
    """5785 -> 'תשפה' (thousands dropped, no punctuation)."""
    n = year % 1000
    out = ""
    while n >= 400:
        out += "ת"; n -= 400
    if n >= 100:
        out += _HUNDREDS[(n // 100) * 100]; n %= 100
    if n in (15, 16):
        return out + ("טו" if n == 15 else "טז")
    if n >= 10:
        out += _TENS[n // 10 - 1]; n %= 10
    if n:
        out += _ONES[n - 1]
    return out


def candidate_years(date: str | None) -> set[str]:
    """A Gregorian date falls in one of two Hebrew years (the new year starts in Sept/Oct)."""
    if not date or len(date) < 4 or not date[:4].isdigit():
        return set()
    y = int(date[:4])
    return {hebrew_year_letters(y + 3760), hebrew_year_letters(y + 3761)}


def cited_in_text(a: Amendment, cited_pdf_ids: set[str], cited_pubs: set[tuple[str, str, str]]) -> bool:
    if a.pdf_id and a.pdf_id in cited_pdf_ids:
        return True
    # otherwise: the text cites the same official publication (series, year, page)
    if not a.page or not str(a.page).strip().isdigit():
        return False
    series = SERIES_ABBR.get((a.series or "").strip(), "")
    years = candidate_years(a.publication_date)
    page = str(int(str(a.page).strip()))
    return any(p == page and y in years and (not series or not s or s == series)
               for s, y, p in cited_pubs)


def verify(item: LawItem, cited_pdf_ids: set[str],
           cited_pubs: set[tuple[str, str, str]] | None = None) -> tuple[str, dict]:
    """Check that every official amendment is cited by the Wikisource text, either by
    its official PDF or by its publication reference (series, year, page).

    verified    every checkable amendment is cited
    outdated    at least one official amendment is missing from the text
    """
    cited_pubs = cited_pubs or set()
    checkable = [a for a in item.amendments if a.pdf_id or (a.page and str(a.page).strip().isdigit())]
    missing = [a for a in checkable if not cited_in_text(a, cited_pdf_ids, cited_pubs)]
    note = {
        "official_amendments": len(item.amendments),
        "checked": len(checkable),
        "missing": [{"name": a.name, "date": a.publication_date, "pdf": a.pdf_url,
                     "ref": f"{a.series or ''} {a.magazine or ''}, {a.page or ''}".strip()} for a in missing],
        "unchecked": len(item.amendments) - len(checkable),
    }
    return ("outdated" if missing else "verified"), note

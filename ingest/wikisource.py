"""Fetch a law page from Hebrew Wikisource by the URL the Knesset site links to.

We store the revision id and time, so every answer points to the exact version
of the text that was indexed, and later edits can't change it silently.

If the Knesset's link is broken (e.g. a title cut short), we look for the page
tagged with the same Knesset law id ({{ח:מאגר|ID}}) and accept it only if
exactly one page carries that tag.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import requests

from .http import get

API = "https://he.wikisource.org/w/api.php"
HOSTS = {"he.wikisource.org", "he.m.wikisource.org"}
# Wikimedia asks automated clients to identify themselves
USER_AGENT = "Il_legislation-ingest/0.3 (public legislation Q&A; github.com/AdiSG1/Il_legislation)"


@dataclass
class Page:
    title: str
    revision_id: int
    revision_time: str
    wikitext: str
    found_by: str = "knesset_link"      # knesset_link | knesset_id_search

    @property
    def permalink(self) -> str:
        return f"https://he.wikisource.org/w/index.php?oldid={self.revision_id}"


def title_from_url(url: str) -> str | None:
    u = urlparse(url)
    if u.netloc not in HOSTS or not u.path.startswith("/wiki/"):
        return None
    return unquote(u.path[len("/wiki/"):]).replace("_", " ").split("#")[0]


class Wikisource:
    def __init__(self, pause: float = 1.0) -> None:
        self.pause = pause
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT})

    def _query(self, params: dict) -> dict:
        r = get(self.http, API, params={"format": "json", "formatversion": "2", **params})
        time.sleep(self.pause)
        return r.json()

    def page(self, title: str) -> Page:
        data = self._query({
            "action": "query", "redirects": "1", "prop": "revisions",
            "rvprop": "ids|timestamp|content", "rvslots": "main", "titles": title,
        })
        p = data["query"]["pages"][0]
        if p.get("missing") or "revisions" not in p:
            raise LookupError(f"Wikisource page not found: {title}")
        rev = p["revisions"][0]
        return Page(p["title"], rev["revid"], rev["timestamp"], rev["slots"]["main"]["content"])

    def find_by_knesset_id(self, israel_law_id: int) -> Page | None:
        data = self._query({
            "action": "query", "list": "search", "srlimit": "5", "srnamespace": "0",
            "srsearch": f'insource:"ח:מאגר|{int(israel_law_id)}"',
        })
        hits = [h["title"] for h in data.get("query", {}).get("search", [])]
        pages = []
        for t in hits:
            pg = self.page(t)
            m = re.search(r"\{\{ח:מאגר\|(\d+)", pg.wikitext)
            if m and int(m.group(1)) == int(israel_law_id):
                pages.append(pg)
        if len(pages) != 1:
            return None
        pages[0].found_by = "knesset_id_search"
        return pages[0]

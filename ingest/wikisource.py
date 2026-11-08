"""Fetch a law page from Hebrew Wikisource by the exact URL the Knesset site links to.

We store the revision id and time, so every answer points to the exact version
of the text that was indexed, and later edits can't change it silently.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import requests

from .http import get

API = "https://he.wikisource.org/w/api.php"
# Wikimedia asks automated clients to identify themselves
USER_AGENT = "Il_legislation-ingest/0.2 (public legislation Q&A; github.com/AdiSG1/Il_legislation)"


@dataclass
class Page:
    title: str
    revision_id: int
    revision_time: str
    wikitext: str

    @property
    def permalink(self) -> str:
        return f"https://he.wikisource.org/w/index.php?oldid={self.revision_id}"


def title_from_url(url: str) -> str | None:
    u = urlparse(url)
    if u.netloc != "he.wikisource.org" or not u.path.startswith("/wiki/"):
        return None
    return unquote(u.path[len("/wiki/"):]).replace("_", " ").split("#")[0]


class Wikisource:
    def __init__(self, pause: float = 0.5) -> None:
        self.pause = pause
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": USER_AGENT})

    def page(self, title: str) -> Page:
        params = {
            "action": "query", "format": "json", "formatversion": "2", "redirects": "1",
            "prop": "revisions", "rvprop": "ids|timestamp|content", "rvslots": "main",
            "titles": title,
        }
        r = get(self.http, API, params=params)
        time.sleep(self.pause)
        p = r.json()["query"]["pages"][0]
        if p.get("missing"):
            raise LookupError(f"Wikisource page not found: {title}")
        rev = p["revisions"][0]
        return Page(p["title"], rev["revid"], rev["timestamp"], rev["slots"]["main"]["content"])

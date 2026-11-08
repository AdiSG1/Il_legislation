"""Shared HTTP GET with retries for brief network errors and server hiccups."""

from __future__ import annotations

import time

import requests

RETRY_STATUS = {429, 500, 502, 503, 504}


def get(session: requests.Session, url: str, *, params=None, timeout=60, tries=5, **kw) -> requests.Response:
    for attempt in range(tries):
        try:
            r = session.get(url, params=params, timeout=timeout, **kw)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == tries - 1:
                raise
            time.sleep(2 ** attempt)
            continue
        if r.status_code in RETRY_STATUS and attempt < tries - 1:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError("unreachable")

"""Shared HTTP GET with retries for brief network errors, rate limits and server hiccups."""

from __future__ import annotations

import time

import requests

RETRY_STATUS = {429, 500, 502, 503, 504}


def _wait(resp: requests.Response | None, attempt: int) -> float:
    if resp is not None and resp.status_code == 429:
        ra = resp.headers.get("Retry-After", "")
        if ra.replace(".", "", 1).isdigit():
            return min(float(ra), 300)
        return min(30 * (attempt + 1), 300)          # 30s, 60s, 90s ...
    return min(2 ** attempt, 60)


def get(session: requests.Session, url: str, *, params=None, timeout=60, tries=6, **kw) -> requests.Response:
    for attempt in range(tries):
        try:
            r = session.get(url, params=params, timeout=timeout, **kw)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == tries - 1:
                raise
            time.sleep(_wait(None, attempt))
            continue
        if r.status_code in RETRY_STATUS and attempt < tries - 1:
            wait = _wait(r, attempt)
            if r.status_code == 429:
                print(f"          {url.split('/')[2]}: too many requests, waiting {int(wait)}s ...", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError("unreachable")

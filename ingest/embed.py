"""Embeddings via the Voyage AI REST API (multilingual, handles Hebrew). 1024 dims.

Plain HTTPS with `requests`, so no SDK version limits on Python.

Rate limits: accounts without a payment method are limited to a few requests
and ~10K tokens per minute. We send requests in token-sized batches and, on
429 "Too Many Requests", wait (honoring Retry-After) and try again.
"""

from __future__ import annotations

import os
import time

import requests

URL = "https://api.voyageai.com/v1/embeddings"
MODEL = os.environ.get("EMBED_MODEL", "voyage-3.5")
MAX_ITEMS = 64
MAX_TOKENS = int(os.environ.get("EMBED_BATCH_TOKENS", "8000"))   # per request, estimated
TRIES = 10


def _est_tokens(text: str) -> int:
    return len(text) // 2 + 1          # Hebrew is token-dense; err on the high side


def _batches(texts: list[str]):
    batch, tokens = [], 0
    for t in texts:
        n = _est_tokens(t)
        if batch and (len(batch) >= MAX_ITEMS or tokens + n > MAX_TOKENS):
            yield batch
            batch, tokens = [], 0
        batch.append(t)
        tokens += n
    if batch:
        yield batch


def _post(texts: list[str], input_type: str) -> list[list[float]]:
    for attempt in range(TRIES):
        try:
            resp = requests.post(
                URL,
                headers={"Authorization": f"Bearer {os.environ['VOYAGE_API_KEY']}"},
                json={"input": texts, "model": MODEL, "input_type": input_type, "output_dimension": 1024},
                timeout=120,
            )
        except (requests.ConnectionError, requests.Timeout):
            time.sleep(min(60, 2 ** attempt))
            continue
        if resp.status_code == 429 and attempt < TRIES - 1:
            wait = resp.headers.get("Retry-After")
            wait = float(wait) if wait and wait.replace(".", "", 1).isdigit() else 21 * (1 + attempt // 3)
            print(f"          embeddings: rate limited, waiting {int(wait)}s ...", flush=True)
            time.sleep(wait)
            continue
        if resp.status_code in (500, 502, 503, 504) and attempt < TRIES - 1:
            time.sleep(min(60, 2 ** attempt))
            continue
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]
    raise RuntimeError("Voyage AI kept refusing requests (rate limit). Add a payment method in Voyage, then rerun.")


def embed_documents(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for batch in _batches(texts):
        out.extend(_post(batch, "document"))
    return out


def embed_query(text: str) -> list[float]:
    return _post([text], "query")[0]

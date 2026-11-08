"""Embeddings via the Voyage AI REST API (multilingual, handles Hebrew). 1024 dims.

Plain HTTPS with `requests`, so no SDK version limits on Python.
"""

from __future__ import annotations

import os
import time

import requests

URL = "https://api.voyageai.com/v1/embeddings"
MODEL = os.environ.get("EMBED_MODEL", "voyage-3.5")
BATCH = 64


def _post(texts: list[str], input_type: str) -> list[list[float]]:
    for attempt in range(5):
        resp = requests.post(
            URL,
            headers={"Authorization": f"Bearer {os.environ['VOYAGE_API_KEY']}"},
            json={"input": texts, "model": MODEL, "input_type": input_type, "output_dimension": 1024},
            timeout=120,
        )
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < 4:
            time.sleep(2 ** attempt * 2)
            continue
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]
    raise RuntimeError("unreachable")


def embed_documents(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        out.extend(_post(texts[i:i + BATCH], "document"))
    return out


def embed_query(text: str) -> list[float]:
    return _post([text], "query")[0]

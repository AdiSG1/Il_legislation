"""Embeddings via Voyage AI (multilingual, handles Hebrew). 1024 dims."""

from __future__ import annotations

import os

MODEL = os.environ.get("EMBED_MODEL", "voyage-3.5")
BATCH = 64


def embed_documents(texts: list[str]) -> list[list[float]]:
    import voyageai

    client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        res = client.embed(texts[i:i + BATCH], model=MODEL, input_type="document", output_dimension=1024)
        out.extend(res.embeddings)
    return out

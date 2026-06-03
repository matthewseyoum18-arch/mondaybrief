"""Semantic similarity via sentence-transformers + pgvector.

Repos:
  - sentence-transformers: https://github.com/UKPLab/sentence-transformers (Apache-2.0)
  - pgvector: https://github.com/pgvector/pgvector (PostgreSQL License)
  - BGE small model: https://huggingface.co/BAAI/bge-small-en-v1.5 (MIT)

We embed every customer row once at upload time and store the vector in the
``customers.embedding`` column. For each new lead, we embed the lead's
"name + category + address" string and ask pgvector to find the cleaner's
single closest existing customer using cosine distance.

That "closest customer" becomes a few-shot exemplar in the Claude scoring
prompt — "this new dental clinic looks most like {existing}, which you clean
for $2,100/mo" — which makes the opener more grounded.
"""
from __future__ import annotations
from functools import lru_cache
import numpy as np
from sentence_transformers import SentenceTransformer

from ..config import get_settings
from ..db import connect
from ..models import Customer, EnrichedLead

EMBED_DIM = 384  # BGE small


@lru_cache
def _model() -> SentenceTransformer:
    return SentenceTransformer(get_settings().embedding_model)


def _to_text(name: str, category: str | None, address: str | None) -> str:
    return f"{name or ''} | {category or ''} | {address or ''}".strip(" |")


def embed(text: str) -> np.ndarray:
    vec = _model().encode(text, normalize_embeddings=True)
    return np.asarray(vec, dtype=np.float32)


def embed_many(texts: list[str]) -> np.ndarray:
    vecs = _model().encode(texts, normalize_embeddings=True, batch_size=32)
    return np.asarray(vecs, dtype=np.float32)


def store_customer_embeddings(customers: list[Customer]) -> int:
    """Write embeddings for any customer rows that don't have one yet."""
    if not customers:
        return 0
    texts = [_to_text(c.name, c.category, c.address) for c in customers]
    vecs = embed_many(texts)
    written = 0
    with connect() as conn:
        for c, vec in zip(customers, vecs):
            if c.id is None:
                continue
            conn.execute(
                "UPDATE customers SET embedding = %s WHERE id = %s AND embedding IS NULL",
                (vec, c.id),
            )
            written += conn.cursor().rowcount
    return written


def nearest_customer(
    client_id: str,
    lead: EnrichedLead,
    *,
    max_cosine_distance: float = 0.55,
) -> tuple[int | None, float | None]:
    """Return (customer_id, distance) for the closest existing customer.

    `distance` is cosine distance in [0, 2]. Lower = more similar.
    Returns (None, None) if nothing crosses the threshold.
    """
    category = (lead.raw_json or {}).get("license_description") or ""
    text = _to_text(lead.name, category, lead.address)
    vec = embed(text)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id, embedding <=> %s AS distance
            FROM customers
            WHERE client_id = %s AND embedding IS NOT NULL
            ORDER BY embedding <=> %s
            LIMIT 1
            """,
            (vec, client_id, vec),
        ).fetchone()
    if row is None:
        return (None, None)
    cid, dist = int(row[0]), float(row[1])
    if dist > max_cosine_distance:
        return (None, None)
    return (cid, dist)

"""Vector store for retrieval.

`PgVectorStore` is the real path: hand-written PostgreSQL using pgvector's
`<=>` cosine operator, the same "raw SQL where it earns its keep" choice
as app/services/analytics.py. `InMemoryStore` is the fallback for the
SQLite dev DB and the test suite -- it reads the same `content_chunks`
rows and does the cosine in Python (the corpus is ~100 short chunks, so
this is sub-millisecond).

Which one is used is decided by the live database dialect, not a config
flag: Postgres gets pgvector, anything else gets the in-memory cosine.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text

from app.extensions import db
from app.models import ContentChunk


@dataclass(frozen=True)
class RetrievedChunk:
    source: str
    kind: str
    title: str
    chunk_index: int
    content: str
    score: float


class VectorStore(Protocol):
    def search(self, query_embedding: list[float], k: int) -> list[RetrievedChunk]: ...


def _cosine(a: list[float], b) -> float:
    b = list(b)
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


class InMemoryStore:
    def search(self, query_embedding: list[float], k: int) -> list[RetrievedChunk]:
        rows = ContentChunk.query.filter(ContentChunk.embedding.isnot(None)).all()
        scored = [
            (
                _cosine(query_embedding, row.embedding),
                row,
            )
            for row in rows
        ]
        scored.sort(key=lambda t: t[0], reverse=True)
        return [
            RetrievedChunk(
                source=row.source,
                kind=row.kind,
                title=row.title,
                chunk_index=row.chunk_index,
                content=row.content,
                score=float(score),
            )
            for score, row in scored[: max(0, k)]
        ]


class PgVectorStore:
    def search(self, query_embedding: list[float], k: int) -> list[RetrievedChunk]:
        vec_literal = "[" + ",".join(f"{x:.8g}" for x in query_embedding) + "]"
        sql = text(
            """
            SELECT source, kind, title, chunk_index, content,
                   1 - (embedding <=> CAST(:qv AS vector)) AS score
            FROM content_chunks
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:qv AS vector)
            LIMIT :k
            """
        )
        rows = db.session.execute(sql, {"qv": vec_literal, "k": max(0, k)}).all()
        return [
            RetrievedChunk(
                source=r.source,
                kind=r.kind,
                title=r.title,
                chunk_index=r.chunk_index,
                content=r.content,
                score=float(r.score),
            )
            for r in rows
        ]


def build_store(config=None) -> VectorStore:
    if db.engine.dialect.name == "postgresql":
        return PgVectorStore()
    return InMemoryStore()

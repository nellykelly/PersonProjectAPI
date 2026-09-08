"""Query -> embed -> top-k chunks -> a numbered context block."""
from __future__ import annotations

from dataclasses import dataclass

from .embeddings import Embedder
from .store import RetrievedChunk, VectorStore


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    context_text: str


def format_context(chunks: list[RetrievedChunk]) -> str:
    parts = []
    for i, c in enumerate(chunks, start=1):
        parts.append(f"[{i}] {c.title}\n{c.content.strip()}")
    return "\n\n".join(parts)


def retrieve(query: str, *, k: int, embedder: Embedder, store: VectorStore) -> RetrievalResult:
    query_embedding = embedder.embed_query(query)
    chunks = store.search(query_embedding, k)
    return RetrievalResult(chunks=chunks, context_text=format_context(chunks))

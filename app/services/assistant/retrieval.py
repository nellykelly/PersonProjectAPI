"""Query -> embed -> top-k chunks -> a numbered context block.

`retrieve()` always returning exactly `k` chunks (the store's raw top-k, no
matter how weak the worst of them scored) was a real contributor to Hera's
per-turn token bill -- see prompts.py's module docstring and the T1 battle
plan note: a plain two-word question still paid for five whole passages of
context, several of them barely related. The keyword-only params added
below (all optional, all default to `None`/`False` so `retrieve(q, k=5,
embedder=e, store=s)` behaves exactly as before) let a caller trim that
without touching the ranking itself:

  - `min_score` -- an absolute floor; a chunk that doesn't clear it is
    dropped outright, however few chunks that leaves.
  - `score_margin` -- a *relative* floor: once the absolute floor has run,
    keep only chunks scoring within `margin` of whatever the best remaining
    score is. Mirrors `orchestrator._SOURCE_MARGIN`'s "near the top" idea,
    applied to what the model reads instead of what gets cited.
  - `max_context_chars` -- a hard cap on the *rendered* context block's
    length, trimming whole chunks starting with the lowest-scoring one
    (chunks arrive best-first from the store) until it fits, rather than
    truncating mid-passage.
  - `skip_for_small_talk` -- for a greeting or a "who/what are you" question,
    site content can't help and doesn't get read anyway; skip the embed +
    search entirely and hand back an empty result.

See `retrieval_params(config)` at the bottom for the config keys a caller
(the orchestrator) reads to build these as one kwargs dict.
"""
from __future__ import annotations

from dataclasses import dataclass

from .embeddings import Embedder
from .store import RetrievedChunk, VectorStore

# Categories from analytics.classify_message that mean "this turn's own
# words carry nothing site content could ground an answer in" -- a greeting
# has no question in it at all, and a "who are you"/"what model is this"
# meta_bot question is about Hera herself, not anything in the corpus.
# Deliberately narrow: every other category (including the "off_topic"
# catch-all, which also covers plenty of legitimate but oddly-phrased
# content questions) still gets a real retrieval pass.
_SMALL_TALK_CATEGORIES = {"greeting", "meta_bot"}


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    context_text: str


def format_context(chunks: list[RetrievedChunk]) -> str:
    parts = []
    for i, c in enumerate(chunks, start=1):
        parts.append(f"[{i}] {c.title}\n{c.content.strip()}")
    return "\n\n".join(parts)


def _apply_relevance_floor(
    chunks: list[RetrievedChunk],
    *,
    min_score: float | None,
    score_margin: float | None,
) -> list[RetrievedChunk]:
    filtered = chunks
    if min_score is not None:
        filtered = [c for c in filtered if c.score >= min_score]
    if score_margin is not None and filtered:
        # `chunks` arrives sorted best-first (both VectorStore
        # implementations sort descending), and filtering preserves order,
        # so the first surviving element is the best remaining score.
        best = filtered[0].score
        filtered = [c for c in filtered if c.score >= best - score_margin]
    return filtered


def _trim_to_char_budget(
    chunks: list[RetrievedChunk], max_chars: int
) -> tuple[list[RetrievedChunk], str]:
    """Drop whole chunks, lowest-scoring first, until the rendered context
    fits `max_chars` -- never truncates a chunk mid-passage, since a
    half-sentence of "context" is worse than one fewer whole passage."""
    kept = list(chunks)
    text = format_context(kept)
    while kept and len(text) > max_chars:
        kept.pop()  # chunks is best-first, so this drops the weakest one
        text = format_context(kept)
    return kept, text


def retrieve(
    query: str,
    *,
    k: int,
    embedder: Embedder,
    store: VectorStore,
    min_score: float | None = None,
    score_margin: float | None = None,
    max_context_chars: int | None = None,
    skip_for_small_talk: bool = False,
) -> RetrievalResult:
    if skip_for_small_talk:
        # Imported here, not at module level, to keep retrieval.py's own
        # import graph light for callers (tests included) that only care
        # about the embed/search path and never touch analytics.
        from .analytics import classify_message

        category = classify_message(query).get("category")
        if category in _SMALL_TALK_CATEGORIES:
            return RetrievalResult(chunks=[], context_text="")

    query_embedding = embedder.embed_query(query)
    chunks = store.search(query_embedding, k)
    chunks = _apply_relevance_floor(chunks, min_score=min_score, score_margin=score_margin)
    context_text = format_context(chunks)
    if max_context_chars is not None:
        chunks, context_text = _trim_to_char_budget(chunks, max_context_chars)
    return RetrievalResult(chunks=chunks, context_text=context_text)


def retrieval_params(config) -> dict:
    """The four `retrieve()` keyword params above, read from config with the
    measured defaults T1 picked (see the battle plan report for the numbers
    behind them) -- so a caller just does
    `retrieve(q, k=..., embedder=..., store=..., **retrieval_params(config))`.
    Read with `config.get(..., default)`, not `config[...]`, since these
    keys don't need to exist in every Config subclass or test fixture."""
    return {
        "min_score": float(config.get("ASSISTANT_RETRIEVAL_MIN_SCORE", 0.4)),
        "score_margin": float(config.get("ASSISTANT_RETRIEVAL_SCORE_MARGIN", 0.08)),
        "max_context_chars": int(config.get("ASSISTANT_MAX_CONTEXT_CHARS", 2600)),
        "skip_for_small_talk": bool(
            config.get("ASSISTANT_SKIP_RETRIEVAL_FOR_SMALL_TALK", True)
        ),
    }

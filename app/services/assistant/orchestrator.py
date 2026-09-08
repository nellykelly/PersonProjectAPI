"""The RAG loop: validate input -> retrieve -> assemble prompt -> generate.

Nothing here does access control or rate limiting -- that's the route's
job (app/blueprints/assistant/routes.py). This function trusts its caller
and takes `is_admin` only so it can be recorded in the query log; Phase 1
registers no tools, so it changes nothing about the answer yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .backends import build_backend
from .embeddings import build_embedder
from .errors import AssistantInputError
from .prompts import build_messages
from .retrieval import retrieve
from .store import build_store

_ALLOWED_ROLES = {"user", "assistant"}
_MAX_TURN_CHARS = 2000


@dataclass
class AssistantAnswer:
    reply: str
    sources: list[dict] = field(default_factory=list)
    backend: str = ""
    model: str = ""
    n_chunks: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


_SOURCE_MARGIN = 0.05
_MAX_SOURCES = 3

# Distinctive terms per content source. A project page is only cited if
# the retrieval liked it *and* the answer actually mentions it -- so
# "Beeznest" can't get tacked onto an answer about the trading systems
# just because its chunk scraped into the top-k. bio/faq carry no terms
# and are always eligible (they legitimately back a lot of answers).
_SOURCE_TERMS = {
    "projects/trading-simulator": (
        "trading", "pnl", "black-scholes", "black scholes", "greeks", "option",
        "watchlist", "risk request", "instrument",
    ),
    "projects/company-scorer": (
        "company scorer", "edgar", "xbrl", "valuation", "backtest", "scoring",
        "quant score", "filings",
    ),
    "projects/pipeline-world": (
        "pipeline world", "pipeline", "sdlc", "ci/cd", "cicd", "character",
        "production town", "seven-stage", "7-stage", "socket.io", "deploy stage",
    ),
    "projects/sre-infra": (
        "sre", "infra layer", "infrastructure layer", "redis", "queue", "rq ",
        "cache-aside", "cache aside", "rate limit", "worker pool", "fakeredis",
    ),
    "projects/site-traffic": (
        "site traffic", "network sniffer", "traffic analytics", "latency",
        "percentile", "ring buffer", "wiretapping", "before_request",
    ),
    "projects/timed-squares": (
        "timed-squares", "timed squares", "obstacle", "telegraph", "grid",
        "survival game", "leaderboard", "turn-based",
    ),
    "projects/beeznest": ("beeznest", "b2b", "rails", "ruby on rails", "streetcode", "accelerator"),
    "bio": (),
    "faq": (),
}


def _pick_sources(chunks, reply_text: str) -> list[dict]:
    """Which retrieved chunks to cite. Dedupe by source, keep the ones the
    retrieval scored near the top, prefer project pages -- then keep a
    project page only if a distinctive term for it shows up in the answer,
    so the citation actually corresponds to what was said. Falls back to
    the single best hit if that filter removes everything. Capped at three."""
    if not chunks:
        return []

    seen: set[str] = set()
    ranked: list = []
    for c in chunks:
        if c.source in seen:
            continue
        seen.add(c.source)
        ranked.append(c)

    best = ranked[0].score
    near = [c for c in ranked if c.score >= best - _SOURCE_MARGIN]
    projects = [c for c in near if c.kind == "project"]
    candidates = projects or near or ranked[:1]

    low = (reply_text or "").lower()

    def mentioned(c) -> bool:
        terms = _SOURCE_TERMS.get(c.source)
        if not terms:  # bio / faq / anything unmapped
            return True
        return any(t in low for t in terms)

    chosen = [c for c in candidates if mentioned(c)] or candidates[:1]
    return [{"title": c.title, "source": c.source} for c in chosen[:_MAX_SOURCES]]


def _clean_history(history, max_turns: int) -> list[dict]:
    if not isinstance(history, list):
        return []
    cleaned: list[dict] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in _ALLOWED_ROLES or not isinstance(content, str):
            continue
        content = content.strip()
        if not content:
            continue
        cleaned.append({"role": role, "content": content[:_MAX_TURN_CHARS]})
    # Keep only the most recent N turns (a turn is a user+assistant pair).
    return cleaned[-(max_turns * 2) :]


def answer(question: str, history, *, config, is_admin: bool = False) -> AssistantAnswer:
    q = (question or "").strip()
    if not q:
        raise AssistantInputError("Please enter a question.")
    max_chars = int(config["ASSISTANT_MAX_INPUT_CHARS"])
    if len(q) > max_chars:
        raise AssistantInputError(f"That question is too long (limit {max_chars} characters).")

    history = _clean_history(history, int(config["ASSISTANT_MAX_HISTORY_TURNS"]))

    embedder = build_embedder(config)
    store = build_store(config)
    result = retrieve(
        q,
        k=int(config["ASSISTANT_RETRIEVAL_TOP_K"]),
        embedder=embedder,
        store=store,
    )

    backend = build_backend(config)
    messages = build_messages(
        question=q,
        context_text=result.context_text,
        history=history,
        is_admin=is_admin,
    )
    reply = backend.generate(
        messages, max_tokens=int(config["ASSISTANT_MAX_OUTPUT_TOKENS"])
    )

    sources = _pick_sources(result.chunks, reply.text)

    return AssistantAnswer(
        reply=reply.text.strip(),
        sources=sources,
        backend=backend.name,
        model=reply.model,
        n_chunks=len(result.chunks),
        prompt_tokens=reply.prompt_tokens,
        completion_tokens=reply.completion_tokens,
    )

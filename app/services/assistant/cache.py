"""Repeat-answer cache for the personal AI assistant.

Every uncached turn costs roughly 2,000-6,000 Groq prompt tokens against an
8,000-tokens-per-minute cap on the chat model, and in practice a lot of
visitors ask the same handful of opening questions ("what do you do",
"what's your best project", "can I see your resume"). A cached answer to
one of those costs zero tokens the second time. This module is the
standalone cache: it knows how to compute a key, read, and write. It does
NOT decide when to call `store()` after a real turn, and the orchestrator
does not call `lookup()`/`store()` yet either -- a later integration task
wires both in, using `is_cacheable_turn()` below to decide.

Storage is the same Redis connection everything else in the app shares
(`app.services.queue.get_redis_connection()`): real Redis in Docker/prod,
an in-process `fakeredis` instance locally and under test (see
app/services/queue.py for why). Keys look like
``assistant:answer:v1:<sha256 hex>`` -- the ``v1`` lets a future format
change bump the prefix instead of trying to interpret old payloads.

Cache key. The key hashes together:
  - the **normalized question** (Unicode NFKC, lowercased, internal
    whitespace collapsed to single spaces, and surrounding quote/
    punctuation characters -- including a trailing "?", "!", or "." --
    stripped), so "What does he do?" and "  what does he do " land on the
    same entry;
  - a **content version**, one cheap aggregate query over
    `app.models.ContentChunk` (row count, max id, max updated_at) --
    any reindex changes at least one of those, which invalidates every
    cached answer without having to enumerate or hash the corpus itself;
  - a **prompt version**, the sha256 of `prompts.SYSTEM_PROMPT` (imported
    lazily -- another task edits prompts.py concurrently with this one,
    and importing only at call time means this module never has to agree
    with that work-in-progress about anything beyond the name
    `SYSTEM_PROMPT` continuing to exist); and
  - a **config fingerprint** covering every setting that can change the
    shape of an answer: `GROQ_MODEL`, `GROQ_TOOL_MODEL`,
    `ASSISTANT_RETRIEVAL_TOP_K`, `ASSISTANT_RETRIEVAL_MIN_SCORE`,
    `ASSISTANT_RETRIEVAL_SCORE_MARGIN`, `ASSISTANT_MAX_CONTEXT_CHARS`,
    `ASSISTANT_SKIP_RETRIEVAL_FOR_SMALL_TALK`, and
    `ASSISTANT_MAX_OUTPUT_TOKENS`. A model swap, a retrieval-width or
    -relevance change, or a shorter/longer output budget all change what
    a fresh answer would look like, so a stale cached one -- generated
    under the old settings -- must not survive a tuning change.

Config keys this module reads directly for its own behavior (owned by
app/config.py, read defensively via `config.get(KEY, default)` since this
task doesn't own that file):
  - `ASSISTANT_CACHE_ENABLED` (default `True`)
  - `ASSISTANT_CACHE_TTL_SECONDS` (default `21600`, six hours)

Fail open, always. A visitor should never see an error because the cache
had a bad day: any Redis or database problem during `lookup()` is treated
as a miss, and any problem during `store()` is silently swallowed --
either way a warning is logged (never the question text, which is
visitor-submitted content this module has no business persisting to logs).

Wired in: `app.services.assistant.orchestrator.answer()` calls `lookup()`
before the Prompt Guard check (a hit means the question was already
answered safely, so there's nothing left to screen or generate) and
`store()` after a turn completes, gated on `is_cacheable_turn()` below.
`flask assistant reindex` calls `invalidate_all()` once it finishes
rebuilding the corpus, as a belt-and-suspenders alongside the content-
version check above (the version bump alone is enough to miss on stale
entries; the explicit flush just means nothing stale sits around
un-served-but-present until it happens to be asked for again).

Cross-visitor safety is the whole point of `is_cacheable_turn()`. A cached
answer is served to *any* future visitor who asks something that
normalizes to the same key, so nothing visitor-specific may ever be
stored: no tool results (a traffic summary, a stock quote, a scorer
leaderboard -- all live data, and some of it, like a leaderboard, can
contain other visitors' own text), nothing from a conversation history,
nothing said to the owner. See that function's docstring for the reasoning
behind each individual rule.

Known residual risk (flagged by red-cell review, not fully closed here):
`is_cacheable_turn()` and the Prompt Guard both check for *unsafe* inputs
and *unsafe* outputs (tool data, owner context, injected instructions),
but neither one checks for *correctness*. A guard-clean first-turn
question that happens to produce a confidently wrong, ungrounded answer
-- a hallucination the system prompt's anti-fabrication rules failed to
catch -- is just as cacheable as a correct one, and would be replayed
verbatim to every subsequent visitor asking something that normalizes to
the same key, for up to `ASSISTANT_CACHE_TTL_SECONDS`. The mitigations in
place are the same ones that guard against a one-off hallucination in the
first place (the grounding rules in `prompts.SYSTEM_PROMPT`), plus the
fact that the exposure window is bounded: six hours by default, or until
the next `flask assistant reindex`. There is no additional check here
that re-verifies a cached answer's factual grounding before serving it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import unicodedata

from app.services.queue import get_redis_connection

logger = logging.getLogger(__name__)

_KEY_PREFIX = "assistant:answer:v1:"

# Characters stripped from both ends of a normalized question, after
# whitespace has already been collapsed: plain whitespace (in case
# stripping punctuation exposes more of it), ASCII and curly quotes, and
# trailing sentence punctuation. Deliberately not stripped from the
# middle of the question -- "What's up?" should not lose its apostrophe.
_STRIP_CHARS = " \t\r\n'\"`“”‘’.,!?;:"


def _normalize_question(question: str) -> str:
    """Unicode-normalize, lowercase, collapse whitespace, and strip
    surrounding quote/punctuation characters so that trivially different
    phrasings of the same question ("What does he do?" vs "what does he
    do") hash to the same cache key."""
    text = unicodedata.normalize("NFKC", question or "")
    text = text.lower()
    text = " ".join(text.split())
    text = text.strip(_STRIP_CHARS)
    return text


def _content_version() -> str:
    """One cheap aggregate over ContentChunk that changes whenever the
    corpus does: `flask assistant reindex` deletes and re-inserts every
    row, so a reindex always changes at least one of count/max(id)/
    max(updated_at), even when the visible text ends up identical."""
    from sqlalchemy import func

    from app.extensions import db
    from app.models import ContentChunk

    count, max_id, max_updated = db.session.query(
        func.count(ContentChunk.id),
        func.max(ContentChunk.id),
        func.max(ContentChunk.updated_at),
    ).one()
    return f"{count}:{max_id}:{max_updated.isoformat() if max_updated else 'none'}"


def _prompt_version() -> str:
    """sha256 of the live SYSTEM_PROMPT. Imported lazily -- see the module
    docstring -- so this module only ever depends on the name existing,
    not on prompts.py's current contents."""
    from app.services.assistant.prompts import SYSTEM_PROMPT

    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def _config_fingerprint(config) -> str:
    """Every setting that can change what a fresh answer would look like.
    Deliberately over-inclusive -- an extra key here just means one more
    cache miss the first time a setting is touched, while a missing one
    means a stale answer can silently outlive a tuning change until the
    TTL clears it (see the module docstring's M4 note)."""
    parts = (
        str(config.get("GROQ_MODEL", "")),
        str(config.get("GROQ_TOOL_MODEL", "")),
        str(config.get("ASSISTANT_RETRIEVAL_TOP_K", "")),
        str(config.get("ASSISTANT_RETRIEVAL_MIN_SCORE", "")),
        str(config.get("ASSISTANT_RETRIEVAL_SCORE_MARGIN", "")),
        str(config.get("ASSISTANT_MAX_CONTEXT_CHARS", "")),
        str(config.get("ASSISTANT_SKIP_RETRIEVAL_FOR_SMALL_TALK", "")),
        str(config.get("ASSISTANT_MAX_OUTPUT_TOKENS", "")),
    )
    return "\x1f".join(parts)


def _cache_key(question: str, config) -> str:
    fingerprint = "\x1f".join(
        [
            _normalize_question(question),
            _content_version(),
            _prompt_version(),
            _config_fingerprint(config),
        ]
    )
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
    return f"{_KEY_PREFIX}{digest}"


def lookup(question: str, config) -> dict | None:
    """Return the cached payload for `question`, or None on a miss, a
    disabled cache, or any failure (Redis down, DB unavailable for the
    content-version query, a corrupt entry, ...) -- a failure here must
    look exactly like an ordinary cache miss to the caller."""
    if not config.get("ASSISTANT_CACHE_ENABLED", True):
        return None
    try:
        key = _cache_key(question, config)
        raw = get_redis_connection().get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        logger.warning(
            "assistant answer cache lookup failed; treating as a miss",
            exc_info=True,
        )
        return None


def store(question: str, payload: dict, config) -> None:
    """Cache `payload` (must be JSON-serializable, e.g. {"reply",
    "sources", "charts", "model"}) for `question`, for
    ASSISTANT_CACHE_TTL_SECONDS. A no-op if the cache is disabled, and
    fails open -- never raises -- if Redis or the content/prompt-version
    lookups it needs for the key are unavailable."""
    if not config.get("ASSISTANT_CACHE_ENABLED", True):
        return
    try:
        key = _cache_key(question, config)
        ttl = int(config.get("ASSISTANT_CACHE_TTL_SECONDS", 21600))
        get_redis_connection().set(key, json.dumps(payload), ex=ttl)
    except Exception:
        logger.warning(
            "assistant answer cache store failed; continuing without caching this answer",
            exc_info=True,
        )


def is_cacheable_turn(
    *,
    history,
    is_owner: bool,
    job_tools_authorized: bool,
    tool_trace,
    reply: str | None,
    error: bool,
) -> bool:
    """Decide whether a just-completed turn is safe to cache and serve to
    a *different* future visitor. The caller (the orchestrator/route, not
    this module) still decides whether to actually call `store()` --
    this only says whether it would be safe to. Every rule exists to stop
    one visitor's private context or one moment's live data from leaking
    into an answer some other visitor gets later:

    - `history` must be empty (first turn only). A cached reply was
      generated for a specific first message with no prior context; on
      any later turn, part of what makes the reply correct is the
      conversation that led to it, which the next visitor asking a
      similarly-worded question will not share.
    - `is_owner` must be False. Nelson signed in gets a materially
      different assistant (job-tracker tools, more candor about
      internals, maybe different phrasing) -- that output must never be
      handed to an anonymous visitor asking a similar-sounding question.
    - `job_tools_authorized` must be False. Same reasoning as `is_owner`,
      checked separately because the two can be authorized independently
      depending on how the caller resolves owner status.
    - `tool_trace` must be empty (no tool calls this turn). Every public
      tool (traffic summary, stock quotes, company-scorer leaderboards,
      trading-simulator state, ...) returns live data that is stale,
      wrong, or -- for anything with a leaderboard or shared state --
      potentially *someone else's submitted text* by the time a second
      visitor gets the cached answer. A turn that used tools is never
      safe to replay verbatim.
    - `reply` must be non-empty, and `error` must be False. An error or
      empty reply is not a real answer worth caching, and caching one
      would mean serving a broken response to the next visitor even after
      the underlying problem is fixed.
    """
    if history:
        return False
    if is_owner:
        return False
    if job_tools_authorized:
        return False
    if tool_trace:
        return False
    if error:
        return False
    if not reply or not reply.strip():
        return False
    return True


def invalidate_all() -> int:
    """Delete every cached answer (SCAN + DELETE by prefix, so this never
    needs FLUSHDB and never touches other keys sharing the same Redis).
    For `flask assistant reindex` to call once it finishes rebuilding the
    corpus -- not wired in by this task. Returns the number of keys
    deleted, or 0 on failure (fails open, like everything else here)."""
    try:
        connection = get_redis_connection()
        keys = list(connection.scan_iter(match=f"{_KEY_PREFIX}*"))
        if not keys:
            return 0
        return connection.delete(*keys)
    except Exception:
        logger.warning("assistant answer cache invalidate_all failed", exc_info=True)
        return 0

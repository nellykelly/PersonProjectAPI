"""The RAG loop: validate input -> retrieve -> assemble prompt -> generate.

Nothing here does access control or rate limiting -- that's the route's
job (app/blueprints/assistant/routes.py), or, for the public tool domains
below, each tool's own call into `app.services.assistant.rate_limit`
(sharing a bucket with its equivalent web route). This function trusts its
caller: `is_admin` is recorded in the query log, and `job_tools_authorized`
(the route's `can_use_job_tools()` result) is the only thing that turns
the job-tracker tools on -- when it is false those six schemas are simply
never built, so there is nothing for a crafted message or a retrieved
passage to invoke there.

The **public** tool domains (trading, Pipeline World, Company Scorer,
Timed-Squares, Site Traffic Analytics) are different from the job tracker
in *why* they're gated: there's no authorization check, since they wrap
actions that are already public, unauthenticated web features -- every
domain's schemas are still built and its dispatcher registered on every
call, but each one's ~1.5k-token schema set is only *offered to the
model* (added to the request `tools` actually sent to Groq) when
`_select_tool_domains()` finds this turn's own words actually relevant to
it (see that function for why: the previous "always offer all five"
behavior put ~3.6k tokens of schemas on every single call before the
system prompt or a single retrieved passage was added, which alone
exceeded free-tier Groq's per-minute token cap once retrieval and a
longer system prompt pushed past it). A plain question that matches no
domain gets zero public tool schemas in the request. The tool-calling
model path (the LangGraph StateGraph built below and driven by
`answer()`) still always runs -- there is no plain single-call fallback
-- it just isn't billing Groq tokens for schemas the question gave no
sign of needing.

Around that graph, `answer()` runs a fixed sequence per turn (see its
docstring for the reasons behind the order):

    validate -> cache lookup -> Prompt Guard -> graph (with a turn
    deadline) -> cache store

A cache hit returns without calling any model at all. A flagged guard
verdict returns an in-character redirect without calling the chat model.
Every model round trip inside the graph first checks the turn's wall-clock
deadline (ASSISTANT_TURN_DEADLINE_SECONDS), so a slow turn ends with a
short "ask me to keep going" instead of holding a worker thread for as
many round trips as it wants. `AssistantBusy` (every model rate-limited)
is never swallowed: it propagates out of `answer()` so the route can say
"busy, try again in N seconds" with a 429, and becomes a {"type": "busy"}
event on the streamed path.
"""
from __future__ import annotations

import json
import logging
import math
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import TypedDict

from langgraph.graph import END, StateGraph

from . import cache, guard
from .backends import build_backend
from .embeddings import build_embedder
from .errors import AssistantBusy, AssistantInputError, AssistantUnavailable
from .job_tools import build_job_tools, dispatch_job_tool
from .pipeline_tools import build_pipeline_tools, dispatch_pipeline_tool
from .prompts import build_messages
from .retrieval import retrieval_params, retrieve
from .scorer_tools import build_scorer_tools, dispatch_scorer_tool
from .store import build_store
from .timedsquares_tools import build_timedsquares_tools, dispatch_timedsquares_tool
from .trading_tools import build_trading_tools, dispatch_trading_tool
from .traffic_tools import build_traffic_tools, dispatch_traffic_tool

logger = logging.getLogger(__name__)

_ALLOWED_ROLES = {"user", "assistant"}
_MAX_TURN_CHARS = 2000

# Defaults for the two config keys this module owns the meaning of (both
# defined in app/config.py; read with .get() so a bare dict config in a
# test still works).
_DEFAULT_MAX_HISTORY_CHARS = 3000
_DEFAULT_TURN_DEADLINE_SECONDS = 40.0

# How many model<->tool round trips a single chat turn may take before we
# stop and return whatever text we have. Four covers "parse a pasted list,
# read it back, then write on confirmation" with headroom.
_MAX_TOOL_ITERS = 4

# What Hera says when Prompt Guard flags the visitor's message. Her voice
# (prompts.SYSTEM_PROMPT: calm, dry, unbothered, one redirect), one line,
# and no em dashes (the site's copy convention). It deliberately doesn't
# say *why* -- telling someone probing for injections exactly which
# message tripped a classifier just helps them tune the next one.
_GUARD_REDIRECT = (
    "I'll pass on that one. Ask me about Nelson's projects, his experience, "
    "or how something on this site works."
)

# What Hera says when the turn runs out of wall-clock time before the
# model finished (ASSISTANT_TURN_DEADLINE_SECONDS). Same voice rules.
_DEADLINE_TEXT = (
    "That one took longer than I get for a single answer, so I stopped there. "
    "Ask me to keep going, or narrow it down a little."
)

# Used when AssistantBusy carries no retry_after at all (rare -- the
# backends always set one). Matches backends._DEFAULT_RETRY_AFTER_SECONDS.
_DEFAULT_BUSY_RETRY_AFTER = 20


def _new_request_id() -> str:
    return uuid.uuid4().hex


def busy_retry_after(exc: AssistantBusy) -> int:
    """Whole seconds to tell a visitor to wait after `AssistantBusy`: the
    provider's estimate rounded up, never below 1, defaulting to 20 when
    there's no estimate. Shared by the stream path's "busy" event and the
    JSON route's Retry-After header so the two can't disagree."""
    raw = getattr(exc, "retry_after", None)
    if raw is None:
        return _DEFAULT_BUSY_RETRY_AFTER
    try:
        return max(1, int(math.ceil(float(raw))))
    except (TypeError, ValueError):
        return _DEFAULT_BUSY_RETRY_AFTER


@dataclass
class AssistantAnswer:
    reply: str
    sources: list[dict] = field(default_factory=list)
    backend: str = ""
    model: str = ""
    n_chunks: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # Populated from the graph's own final message list -- no extra model
    # call, just formatting state that already exists. Empty whenever the
    # turn made no tool calls. Ordered list of {"tool", "arguments", "result"}
    # dicts, one per tool call actually dispatched, in the order the model
    # made them -- this is what lets a caller show "the model decided to call
    # X, then Y" rather than just the final answer text.
    tool_trace: list[dict] = field(default_factory=list)

    # --- observability (all logged to AssistantQuery by the route) ---
    # uuid4 hex, 32 chars, unique per turn -- lets a visitor-reported
    # problem be matched to its log row.
    request_id: str = field(default_factory=_new_request_id)
    # True if any round trip this turn was answered by a fallback model
    # (backends.FallbackBackend: the primary was rate-limited or failing).
    fell_back: bool = False
    # Served from the repeat-answer cache: no model call, zero tokens.
    cache_hit: bool = False
    # Prompt Guard's verdict on the visitor's message. guard_flagged means
    # the reply is the canned redirect and no chat model ran; guard_error
    # is set when the guard failed open (the turn ran normally).
    guard_flagged: bool = False
    guard_score: float | None = None
    guard_error: str | None = None
    # The turn hit ASSISTANT_TURN_DEADLINE_SECONDS and ended early with
    # the "ask me to keep going" text.
    deadline_hit: bool = False


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
    "projects/tiny-jvm": (
        "tiny jvm", "tinyjvm", "webassembly", "wasm", "bytecode", "stack machine",
        "microcontroller", "wokwi", "gpio", "tlc", "opcode",
    ),
    "projects/market-warehouse": (
        "market data warehouse", "data warehouse", "dbt", "dimensional", "kimball",
        "duckdb", "motherduck", "snowflake", "star schema", "fact table", "sharpe",
        "sortino", "price projection", "risk metrics",
    ),
    "projects/leetcode-150": (
        "leetcode", "interview 150", "top interview", "leetcode tracker",
    ),
    "bio": (),
    "faq": (),
    "resume": (),
}


def _extract_tool_trace(messages: list) -> list[dict]:
    """Walk the graph's final message list and reconstruct which tools were
    called, with what arguments, and what each returned -- in call order.
    Pure formatting over state the graph already built; no extra model call.
    """
    pending: dict[str, dict] = {}
    trace: list[dict] = []
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                pending[tc["id"]] = {"tool": fn.get("name", ""), "raw_arguments": fn.get("arguments", "")}
        elif msg.get("role") == "tool":
            call = pending.get(msg.get("tool_call_id"))
            if call is None:
                continue
            try:
                arguments = json.loads(call["raw_arguments"]) if call["raw_arguments"] else {}
            except ValueError:
                arguments = call["raw_arguments"]
            trace.append({"tool": call["tool"], "arguments": arguments, "result": msg.get("content", "")})
    return trace


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


# Which public tool domain each _SOURCE_TERMS project maps to -- reusing
# those same keyword lists (already tuned to tell one project's content
# apart from another's) rather than maintaining a second, parallel list.
# A few terms are added per domain for words a tool needs but a citation
# never did (e.g. "projection" doesn't appear in _SOURCE_TERMS, which
# already has "price projection" under market-warehouse for a different
# project's citation, but the trading tool get_projection wraps this
# demo's own projection feature).
_TOOL_DOMAIN_SOURCE_KEYS = {
    "trading": "projects/trading-simulator",
    "pipeline": "projects/pipeline-world",
    "scorer": "projects/company-scorer",
    "timedsquares": "projects/timed-squares",
    "traffic": "projects/site-traffic",
}
_TOOL_DOMAIN_EXTRA_TERMS = {
    "trading": ("projection", "quote", "ticker", "portfolio", "position", "stock"),
    "pipeline": ("icebreaker",),
    "scorer": ("fundamentals",),
    "timedsquares": (),
    "traffic": ("traffic", "sniffer"),
}


def _select_tool_domains(question: str, history: list[dict]) -> set[str]:
    """Which of the always-public tool domains this turn's own words
    actually suggest -- see the module docstring for why this exists.
    Deliberately generous (a false positive costs ~0.1-1.6k tokens of
    unused schemas; a false negative costs the model a tool it actually
    needed), so it errs toward including a domain whenever a term for it
    shows up anywhere in the current question or recent history, not just
    an exact intent match."""
    text = " ".join([question] + [h["content"] for h in history]).lower()
    matched = set()
    for domain, source_key in _TOOL_DOMAIN_SOURCE_KEYS.items():
        terms = _SOURCE_TERMS[source_key] + _TOOL_DOMAIN_EXTRA_TERMS[domain]
        if any(term in text for term in terms):
            matched.add(domain)
    return matched


def _clean_history(history, max_turns: int, max_chars: int | None = None) -> list[dict]:
    """Keep only well-formed user/assistant messages, each capped at
    _MAX_TURN_CHARS, the most recent `max_turns` turns of them, and -- when
    `max_chars` is a positive number -- only as many of the most recent
    messages as fit in `max_chars` total, dropping the oldest first.

    Why a total budget on top of the per-message cap: history is resent on
    every round trip of every turn, and 4 turns x 2 messages x 2,000 chars
    is ~4k tokens, half of the chat model's 8,000 tokens/minute on its
    own. Old turns are the least useful to the current question, so they
    go first; the newest messages are never truncated mid-text (a
    half-sentence reads to the model as something the visitor actually
    said)."""
    cleaned = _clean_history_turns(history, max_turns)
    if max_chars and max_chars > 0:
        total = sum(len(m["content"]) for m in cleaned)
        while cleaned and total > max_chars:
            total -= len(cleaned.pop(0)["content"])
    return cleaned


def _clean_history_turns(history, max_turns: int) -> list[dict]:
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


def _sum_tokens(a: int | None, b: int | None) -> int | None:
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


_DEFAULT_TOOL_FALLBACK = (
    "I couldn't finish that in one pass -- ask me to keep going, or make the "
    "rest of the change on the page."
)

_ONE_AT_A_TIME = "One at a time -- tell me which one first."


def _run_tool_loop(
    backend,
    messages,
    tools,
    max_tokens,
    *,
    dispatch,
    max_iters=_MAX_TOOL_ITERS,
    fallback=_DEFAULT_TOOL_FALLBACK,
    tool_call_limits: dict[str, int] | None = None,
):
    """Drive up to `max_iters` model<->tool round trips. Returns
    `(final_text, prompt_tokens, completion_tokens, model)`.

    `dispatch(name, arguments) -> str` runs one tool call and must never
    raise -- it always hands back a string for the model to read. The
    assistant passes a single dispatcher that routes by tool name; the
    /family chat passes its own (and a higher `max_iters` for pasted-list
    adds). This loop knows nothing about either tool set.

    `tool_call_limits` is an optional per-turn governor: `{tool_name: max_
    calls}`. It is a fresh local counter every call (never persisted
    across turns or shared with any other invocation) -- once a name hits
    its cap *within this one loop*, further calls to that name are not
    dispatched at all; the model is handed back `_ONE_AT_A_TIME` as that
    call's tool result instead, exactly as if the tool itself had refused,
    so it can relay that to the user rather than the call silently
    vanishing. Names absent from `tool_call_limits` (or when it's `None`)
    are uncapped. `fallback` is returned only if the loop hits `max_iters`
    still wanting tools and has no text to show."""
    convo = list(messages)
    p_tok = c_tok = None
    model = ""
    last_text = ""
    call_counts: dict[str, int] = {}

    for _ in range(max_iters):
        reply = backend.generate(convo, max_tokens=max_tokens, tools=tools)
        model = reply.model or model
        p_tok = _sum_tokens(p_tok, reply.prompt_tokens)
        c_tok = _sum_tokens(c_tok, reply.completion_tokens)
        if reply.text:
            last_text = reply.text

        if not reply.tool_calls:
            return reply.text, p_tok, c_tok, model

        convo.append(
            {
                "role": "assistant",
                "content": reply.text or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in reply.tool_calls
                ],
            }
        )
        for tc in reply.tool_calls:
            name = tc["name"]
            cap = (tool_call_limits or {}).get(name)
            if cap is not None and call_counts.get(name, 0) >= cap:
                out = _ONE_AT_A_TIME
            else:
                out = dispatch(name, tc["arguments"])
                call_counts[name] = call_counts.get(name, 0) + 1
            convo.append(
                {"role": "tool", "tool_call_id": tc["id"], "content": out}
            )

    # Ran out of iterations still asking for tools.
    return (last_text or fallback), p_tok, c_tok, model


# Per-turn cap on the writes/spends that matter most: one open_position,
# one join_pipeline_world, one score_company, one run_backtest per chat
# turn. Read-only/idempotent tools (get_quote, list_open_positions,
# get_risk_report, preview_*, check_character_status, get_leaderboard) are
# uncapped here -- they're already governed by their own rate-limit
# buckets (or, for the job tools, by `authz`) and legitimately need
# multiple calls in one turn (e.g. preview, then look something up, then
# the real write).
_TOOL_CALL_LIMITS = {
    "open_position": 1,
    "join_pipeline_world": 1,
    "score_company": 1,
    "run_backtest": 1,
}


# ---------------------------------------------------------------------------
# /assistant's own tool-calling loop, as a LangGraph StateGraph.
#
# This is a from-scratch reimplementation of `_run_tool_loop`'s semantics for
# the `answer()` path ONLY -- /family/chat.py keeps calling `_run_tool_loop`
# directly (above), untouched. Four nodes:
#
#   retrieve -> agent -> tools -> (loop back to agent, or END)
#                  \-------------------------------> END
#
# `retrieve` does the RAG lookup (`retrieve()` in retrieval.py) and builds
# the initial message list -- no LLM call. `agent` is the one
# `backend.generate()` call per round trip that decides answer-or-tool-calls.
# `tools` dispatches every requested call through the same flat
# `dispatch_map` pattern `answer()` has always built, enforcing the
# `tool_call_limits` governor before a capped name is dispatched a second
# time in one turn. The per-turn iteration cap (`_MAX_TOOL_ITERS`) lives as
# first-class state (`iters` / `max_iters` / `exhausted`), checked on the
# edge leaving `tools`, exactly where `_run_tool_loop`'s `for` loop would
# have stopped asking for another round trip.
#
# The turn deadline (`deadline` / `deadline_hit`) is checked at the same
# two places a new `agent` round trip could start: leaving `retrieve` and
# leaving `tools`. Past it, the edge goes to END instead and `_finalize`
# returns _DEADLINE_TEXT. A call already in flight can't be interrupted
# from here; the backend's own per-call timeout bounds that.
#
# `graph.invoke()` is synchronous -- no `.ainvoke()`, no event loop -- to
# match the rest of this WSGI/blocking Flask app (Socket.IO runs in
# `threading` mode). No checkpointer is attached, so the state dict is
# plain in-memory Python for the lifetime of one call; it's fine for it to
# carry live objects (the embedder, the store, the dispatch closure) that
# would never survive serialization.
# ---------------------------------------------------------------------------


class _AssistantGraphState(TypedDict, total=False):
    # Set once, before `invoke()`, and never written by a node afterwards.
    question: str
    history: list
    is_admin: bool
    job_tools_authorized: bool
    embedder: object
    store: object
    retrieval_k: int
    tools: list
    dispatch: object  # Callable[[str, str], str]
    tool_call_limits: dict
    config: object
    max_tokens: int
    max_iters: int
    fallback: str
    # The public tool domains offered this turn (_select_tool_domains) --
    # build_messages uses it to include only those domains' prompt notes.
    offered_domains: set
    # time.monotonic() value after which no new model round trip starts.
    deadline: float

    # Working state, updated as the graph runs.
    messages: list
    chunks: list
    n_chunks: int
    iters: int
    call_counts: dict
    tool_calls: object  # tuple[dict, ...] | None
    last_text: str
    reply_text: str
    model: str
    backend_name: str
    prompt_tokens: int | None
    completion_tokens: int | None
    exhausted: bool
    fell_back: bool
    deadline_hit: bool


def _past_deadline(state: _AssistantGraphState) -> bool:
    deadline = state.get("deadline")
    return deadline is not None and time.monotonic() >= deadline


def _node_retrieve(state: _AssistantGraphState) -> dict:
    result = retrieve(
        state["question"],
        k=state["retrieval_k"],
        embedder=state["embedder"],
        store=state["store"],
        **retrieval_params(state["config"]),
    )
    messages = build_messages(
        question=state["question"],
        context_text=result.context_text,
        history=state["history"],
        is_admin=state["is_admin"],
        job_tools=state["job_tools_authorized"],
        offered_domains=state.get("offered_domains", set()),
    )
    return {
        "messages": messages,
        "chunks": result.chunks,
        "n_chunks": len(result.chunks),
        # Checked here too, not only between tool rounds: the guard call
        # and an embedder cold start both run before the first round trip.
        "deadline_hit": _past_deadline(state),
    }


def _route_after_retrieve(state: _AssistantGraphState) -> str:
    return END if state.get("deadline_hit") else "agent"


def _node_agent(state: _AssistantGraphState) -> dict:
    # Built (and, for groq, cache-looked-up by `(kind, model)`) fresh on
    # every round trip rather than once up front, so that a backend that
    # fails to construct raises at the same point in the call sequence as
    # before this rebuild: retrieve() first, then build_backend(), then the
    # first generate() call -- never earlier.
    backend = build_backend(state["config"], for_tools=True)
    reply = backend.generate(
        state["messages"], max_tokens=state["max_tokens"], tools=state["tools"]
    )

    update: dict = {
        "model": reply.model or state.get("model", ""),
        "backend_name": backend.name,
        "prompt_tokens": _sum_tokens(state.get("prompt_tokens"), reply.prompt_tokens),
        "completion_tokens": _sum_tokens(
            state.get("completion_tokens"), reply.completion_tokens
        ),
        "last_text": reply.text or state.get("last_text", ""),
        "reply_text": reply.text,
        "iters": state.get("iters", 0) + 1,
        "fell_back": bool(state.get("fell_back")) or bool(getattr(reply, "fell_back", False)),
    }
    if reply.tool_calls:
        update["tool_calls"] = reply.tool_calls
        update["messages"] = state["messages"] + [
            {
                "role": "assistant",
                "content": reply.text or "",
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]},
                    }
                    for tc in reply.tool_calls
                ],
            }
        ]
    else:
        update["tool_calls"] = None
    return update


def _route_after_agent(state: _AssistantGraphState) -> str:
    return "tools" if state.get("tool_calls") else END


def _node_tools(state: _AssistantGraphState) -> dict:
    dispatch = state["dispatch"]
    limits = state.get("tool_call_limits") or {}
    call_counts = dict(state.get("call_counts") or {})
    convo = list(state["messages"])
    for tc in state.get("tool_calls") or ():
        name = tc["name"]
        cap = limits.get(name)
        if cap is not None and call_counts.get(name, 0) >= cap:
            out = _ONE_AT_A_TIME
        else:
            out = dispatch(name, tc["arguments"])
            call_counts[name] = call_counts.get(name, 0) + 1
        convo.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
    exhausted = state.get("iters", 0) >= state["max_iters"]
    return {
        "messages": convo,
        "call_counts": call_counts,
        "tool_calls": None,
        "exhausted": exhausted,
        # Only meaningful when another round trip would otherwise start --
        # an exhausted turn was stopped by the iteration cap, not the clock.
        "deadline_hit": (not exhausted) and _past_deadline(state),
    }


def _route_after_tools(state: _AssistantGraphState) -> str:
    if state.get("exhausted") or state.get("deadline_hit"):
        return END
    return "agent"


def _build_assistant_graph():
    builder = StateGraph(_AssistantGraphState)
    builder.add_node("retrieve", _node_retrieve)
    builder.add_node("agent", _node_agent)
    builder.add_node("tools", _node_tools)
    builder.set_entry_point("retrieve")
    builder.add_conditional_edges("retrieve", _route_after_retrieve, {"agent": "agent", END: END})
    builder.add_conditional_edges("agent", _route_after_agent, {"tools": "tools", END: END})
    builder.add_conditional_edges("tools", _route_after_tools, {"agent": "agent", END: END})
    return builder.compile()


# Compiled once at import time. The compiled graph is stateless between
# calls -- every `invoke()` gets its own fresh state dict -- so reusing it
# across requests/threads is safe.
_ASSISTANT_GRAPH = _build_assistant_graph()


def _validate_question(question: str, config) -> str:
    q = (question or "").strip()
    if not q:
        raise AssistantInputError("Please enter a question.")
    max_chars = int(config["ASSISTANT_MAX_INPUT_CHARS"])
    if len(q) > max_chars:
        raise AssistantInputError(f"That question is too long (limit {max_chars} characters).")
    return q


def _prepare_initial_state(
    q: str,
    history,
    *,
    config,
    is_admin: bool,
    job_tools_authorized: bool,
    started: float | None = None,
) -> _AssistantGraphState:
    """Shared setup for `answer()` and `stream_answer()` -- everything that
    has to happen before the graph runs at all: history trimming, building
    the embedder/store, assembling the public tool set (and the job-tracker
    six when authorized) into one schema list plus one dispatch map, and
    the resulting initial graph state. Kept as one function so the two
    entry points can never drift on what tools/limits/config a turn runs
    with -- only how its progress is consumed (final value vs. streamed).

    `started` is the time.monotonic() the turn began (the caller's own
    clock, so time spent on the cache lookup and guard call counts against
    the deadline too); the graph refuses to start a model round trip once
    ASSISTANT_TURN_DEADLINE_SECONDS past it."""
    history = _clean_history(
        history,
        int(config["ASSISTANT_MAX_HISTORY_TURNS"]),
        int(config.get("ASSISTANT_MAX_HISTORY_CHARS", _DEFAULT_MAX_HISTORY_CHARS)),
    )
    if started is None:
        started = time.monotonic()
    deadline = started + float(
        config.get("ASSISTANT_TURN_DEADLINE_SECONDS", _DEFAULT_TURN_DEADLINE_SECONDS)
    )

    embedder = build_embedder(config)
    store = build_store(config)

    # The public tool sets have no authorization gate, unlike the job
    # tracker. Every domain's schemas are still built and its dispatcher
    # registered unconditionally -- that's cheap, pure-Python bookkeeping
    # with no token cost of its own, and keeps dispatch working even if a
    # model call somehow names a tool this turn didn't offer. What's
    # actually gated is which specs get added to `tools` below, i.e. what
    # gets serialized into the request Groq bills tokens for (see
    # _select_tool_domains for why).
    trading_specs = build_trading_tools()
    pipeline_specs = build_pipeline_tools()
    scorer_specs = build_scorer_tools()
    timedsquares_specs = build_timedsquares_tools()
    traffic_specs = build_traffic_tools()

    dispatch_map = {}
    for spec in trading_specs:
        dispatch_map[spec["function"]["name"]] = dispatch_trading_tool
    for spec in pipeline_specs:
        dispatch_map[spec["function"]["name"]] = dispatch_pipeline_tool
    for spec in scorer_specs:
        dispatch_map[spec["function"]["name"]] = dispatch_scorer_tool
    for spec in timedsquares_specs:
        dispatch_map[spec["function"]["name"]] = dispatch_timedsquares_tool
    for spec in traffic_specs:
        dispatch_map[spec["function"]["name"]] = dispatch_traffic_tool

    domains = _select_tool_domains(q, history)
    tools = []
    if "trading" in domains:
        tools += trading_specs
    if "pipeline" in domains:
        tools += pipeline_specs
    if "scorer" in domains:
        tools += scorer_specs
    if "timedsquares" in domains:
        tools += timedsquares_specs
    if "traffic" in domains:
        tools += traffic_specs

    if job_tools_authorized:
        job_specs = build_job_tools(True)
        tools = tools + job_specs
        for spec in job_specs:
            dispatch_map[spec["function"]["name"]] = (
                lambda n, a: dispatch_job_tool(n, a, authorized=True)
            )

    def _dispatch(name: str, arguments) -> str:
        fn = dispatch_map.get(name)
        if fn is None:
            return f"Unknown tool {name!r}."
        return fn(name, arguments)

    # Public tools always exist now, so the tool-calling model path is
    # always used -- there is no more plain single-call fallback. Driven by
    # the LangGraph StateGraph above: retrieve -> agent -> tools -> (loop
    # back to agent, or END), at the same one-generate-call-per-round-trip
    # cost as the old hand-rolled loop.
    max_tokens = int(config["ASSISTANT_MAX_OUTPUT_TOKENS"])

    return {
        "question": q,
        "history": history,
        "is_admin": is_admin,
        "job_tools_authorized": job_tools_authorized,
        "embedder": embedder,
        "store": store,
        "retrieval_k": int(config["ASSISTANT_RETRIEVAL_TOP_K"]),
        "tools": tools,
        "dispatch": _dispatch,
        "tool_call_limits": _TOOL_CALL_LIMITS,
        "config": config,
        "max_tokens": max_tokens,
        "max_iters": _MAX_TOOL_ITERS,
        "fallback": _DEFAULT_TOOL_FALLBACK,
        "offered_domains": domains,
        "deadline": deadline,
        "messages": [],
        "chunks": [],
        "n_chunks": 0,
        "iters": 0,
        "call_counts": {},
        "tool_calls": None,
        "last_text": "",
        "reply_text": "",
        "model": "",
        "backend_name": "",
        "prompt_tokens": None,
        "completion_tokens": None,
        "exhausted": False,
        "fell_back": False,
        "deadline_hit": False,
    }


def _finalize(
    final_state: dict,
    *,
    request_id: str | None = None,
    verdict=None,
) -> AssistantAnswer:
    # Mirrors `_run_tool_loop`'s two exits exactly: a normal stop (no more
    # tool calls) hands back that last reply's text verbatim, even if it's
    # empty; running out of iterations still wanting tools hands back the
    # last non-empty text seen, or the fallback if there was none. A third
    # exit, the turn deadline, hands back _DEADLINE_TEXT -- not last_text,
    # which on a turn cut short mid-tools is usually a preamble ("let me
    # check that") rather than an answer -- and cites nothing, since
    # nothing was actually answered.
    deadline_hit = bool(final_state.get("deadline_hit")) and not final_state.get("exhausted")
    if final_state.get("exhausted"):
        final_text = final_state.get("last_text") or _DEFAULT_TOOL_FALLBACK
    elif deadline_hit:
        final_text = _DEADLINE_TEXT
    else:
        final_text = final_state.get("reply_text", "")

    sources = [] if deadline_hit else _pick_sources(final_state.get("chunks", []), final_text)
    tool_trace = _extract_tool_trace(final_state.get("messages", []))

    answer_ = AssistantAnswer(
        reply=(final_text or "").strip(),
        sources=sources,
        backend=final_state.get("backend_name", ""),
        model=final_state.get("model", ""),
        n_chunks=final_state.get("n_chunks", 0),
        prompt_tokens=final_state.get("prompt_tokens"),
        completion_tokens=final_state.get("completion_tokens"),
        tool_trace=tool_trace,
        fell_back=bool(final_state.get("fell_back")),
        deadline_hit=deadline_hit,
    )
    if request_id:
        answer_.request_id = request_id
    _apply_verdict(answer_, verdict)
    return answer_


# ---------------------------------------------------------------------------
# Cache / guard helpers shared by answer() and the detailed stream path.
# ---------------------------------------------------------------------------


def _cache_eligible(history, *, is_admin: bool, job_tools_authorized: bool) -> bool:
    """Whether this turn could ever be served from, or stored in, the
    shared answer cache -- the pre-turn half of cache.is_cacheable_turn()'s
    rules (first turn, not the owner, no job tools). Checked before
    lookup() too: an owner or a mid-conversation visitor must never be
    handed an answer generated for someone else's context. Uses `history`
    exactly as the caller passed it, before cleaning -- anything non-empty,
    even junk that cleans away to nothing, counts as "not a first turn"."""
    return not history and not is_admin and not job_tools_authorized


def _from_cache(payload, *, request_id: str) -> AssistantAnswer | None:
    """AssistantAnswer for a cache hit, or None if the payload is unusable
    (treated as a miss). Zero tokens: nothing was sent to any model."""
    if not isinstance(payload, dict):
        return None
    reply = payload.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        return None
    sources = payload.get("sources") or []
    if not isinstance(sources, list):
        sources = []
    return AssistantAnswer(
        reply=reply,
        sources=sources,
        backend="",
        model=str(payload.get("model") or ""),
        n_chunks=0,
        prompt_tokens=0,
        completion_tokens=0,
        request_id=request_id,
        cache_hit=True,
    )


def _apply_verdict(answer_: AssistantAnswer, verdict) -> None:
    if verdict is None:
        return
    answer_.guard_flagged = bool(verdict.flagged)
    answer_.guard_score = verdict.score
    answer_.guard_error = verdict.error


def _guard_redirect(verdict, *, request_id: str) -> AssistantAnswer:
    """The in-character redirect for a flagged message: no sources, no
    model, no tokens."""
    answer_ = AssistantAnswer(
        reply=_GUARD_REDIRECT,
        sources=[],
        backend="",
        model="",
        prompt_tokens=0,
        completion_tokens=0,
        request_id=request_id,
    )
    _apply_verdict(answer_, verdict)
    return answer_


# How much recent conversation the Prompt Guard sees alongside the
# current message -- one turn (one user message, one assistant reply) is
# enough to catch a payload planted one step back without turning every
# screen into a scan of an unbounded history. See _build_screen_text.
_SCREEN_HISTORY_TURNS = 1


def _build_screen_text(q: str, history) -> str:
    """Text handed to the Prompt Guard: the current message plus the most
    recent turn of conversation history, not the current message alone.

    Red-cell finding (H1): `history` is entirely client-supplied -- sent
    fresh with every request, never re-derived from a server-side session
    -- so a visitor can plant a fabricated prior turn (a forged
    "assistant" reply that reads like Hera already agreeing to lift her
    own rules) and follow it with an innocuous-looking final message
    ("go ahead"). Screening only that final message let the injection
    straight through: the payload had simply moved one turn earlier,
    outside the guard's field of view. Folding in the last exchange closes
    that gap.

    Reuses `_clean_history_turns` for sanitizing (well-formed role/content
    pairs only, each already capped at `_MAX_TURN_CHARS`) rather than
    re-validating client-supplied JSON a second way here.

    `guard.screen()` truncates its input to its own ~1,800-char window
    (`guard._MAX_INPUT_CHARS`, the model's context budget), keeping
    whichever part comes first -- fine for a lone message, wrong once
    history is prepended: the OLDEST text, exactly where a smuggled
    instruction is most likely to be hiding, would survive that
    truncation while the actual current question got pushed out. So this
    function keeps the TAIL instead, trimming from the front, before
    `guard.screen()` ever gets to apply its own (now redundant, but
    harmless) truncation. Also NFKC-normalizes the combined text, closing
    a homoglyph-style evasion the guard's own truncation didn't touch."""
    recent = _clean_history_turns(history, _SCREEN_HISTORY_TURNS)
    parts = [f"{item['role']}: {item['content']}" for item in recent]
    parts.append(f"user: {q}")
    combined = unicodedata.normalize("NFKC", "\n".join(parts))
    if len(combined) > guard._MAX_INPUT_CHARS:
        combined = combined[-guard._MAX_INPUT_CHARS :]
    return combined


def _screen(text: str, config):
    """guard.screen(), which is documented never to raise -- but the whole
    point of the guard is to fail open, so a bug in it must not become an
    outage either. Looked up on the module at call time so tests can
    monkeypatch `guard.screen`. `text` is the combined screen text from
    `_build_screen_text`, not necessarily the bare visitor message."""
    try:
        return guard.screen(text, config)
    except Exception as exc:  # noqa: BLE001 - fail open, like the guard itself
        logger.warning("assistant guard raised; failing open", exc_info=True)
        return guard.GuardVerdict(flagged=False, score=None, model="", error=str(exc))


def _maybe_store(q: str, history, answer_: AssistantAnswer, *, config, is_admin: bool,
                 job_tools_authorized: bool) -> bool:
    """Store a finished turn's answer in the shared cache when it's safe to
    replay to a different visitor. Beyond cache.is_cacheable_turn()'s
    rules, never stores: a guard redirect or cache hit (not a fresh
    answer), a deadline or iteration-cap text (not an answer at all), or a
    fallback model's answer (the cache key names the primary model, so a
    substitute's reply would otherwise be served for hours as if the
    primary had written it). Returns whether it stored."""
    if answer_.cache_hit or answer_.guard_flagged or answer_.deadline_hit or answer_.fell_back:
        return False
    if answer_.reply in (_DEFAULT_TOOL_FALLBACK, _DEADLINE_TEXT, _GUARD_REDIRECT):
        return False
    if not cache.is_cacheable_turn(
        history=history,
        is_owner=is_admin,
        job_tools_authorized=job_tools_authorized,
        tool_trace=answer_.tool_trace,
        reply=answer_.reply,
        error=False,
    ):
        return False
    cache.store(
        q,
        {
            "reply": answer_.reply,
            "sources": answer_.sources,
            # Always [] for a cacheable turn: charts come from tool calls,
            # and a turn with tool calls is never cacheable.
            "charts": [],
            "model": answer_.model,
        },
        config,
    )
    return True


def answer(
    question: str,
    history,
    *,
    config,
    is_admin: bool = False,
    job_tools_authorized: bool = False,
) -> AssistantAnswer:
    """One chat turn, start to finish. Order, and why:

    1. Validate (raises AssistantInputError -> the route's 400).
    2. Cache lookup, only for a first-turn anonymous question (see
       _cache_eligible). First because a hit means this exact question
       already got a real, safe answer: it costs zero model tokens and
       skips even the guard's request.
    3. Prompt Guard. Flagged -> the canned redirect, no retrieval, no chat
       model. Skipped or failed-open verdicts carry on normally, with the
       score/error recorded on the answer for the log.
    4. The graph, under the turn deadline.
    5. Cache store, when the finished turn is safe to replay.

    Raises AssistantBusy (every model rate-limited) and AssistantUnavailable
    (anything else broken) straight through -- the route turns those into
    a 429 with Retry-After and a 503 respectively. Nothing here converts
    them into reply text."""
    started = time.monotonic()
    q = _validate_question(question, config)
    request_id = _new_request_id()

    eligible = _cache_eligible(history, is_admin=is_admin, job_tools_authorized=job_tools_authorized)
    if eligible:
        hit = _from_cache(cache.lookup(q, config), request_id=request_id)
        if hit is not None:
            return hit

    verdict = _screen(_build_screen_text(q, history), config)
    if verdict.flagged:
        return _guard_redirect(verdict, request_id=request_id)

    initial_state = _prepare_initial_state(
        q,
        history,
        config=config,
        is_admin=is_admin,
        job_tools_authorized=job_tools_authorized,
        started=started,
    )
    final_state = _ASSISTANT_GRAPH.invoke(initial_state)
    result = _finalize(final_state, request_id=request_id, verdict=verdict)

    if eligible and not final_state.get("exhausted"):
        _maybe_store(
            q, history, result, config=config, is_admin=is_admin,
            job_tools_authorized=job_tools_authorized,
        )
    return result


def stream_answer(
    question: str,
    history,
    *,
    config,
    is_admin: bool = False,
    job_tools_authorized: bool = False,
    detailed: bool = False,
):
    """Same validation and setup as `answer()`, but yields progress events as
    the graph actually runs instead of blocking until it's done.

    Two callers, two contracts, picked by `detailed`:

    * `detailed=False` (the default) -- the Market Data Warehouse page's
      "autonomous stock analysis" demo. Its question is server-written, so
      there's nothing to guard-screen or cache, and its event shapes are
      exactly what they always were (market_warehouse.js and its tests
      depend on them): "final" carries only "reply", and a rate limit is
      an ordinary "error" event. The turn deadline, history budget and
      retrieval trimming still apply.
    * `detailed=True` -- the main /assistant chat's streamed endpoint. Runs
      the same cache lookup / Prompt Guard / cache store steps as
      `answer()`, reports AssistantBusy as its own "busy" event, and the
      "final" event carries everything the route needs to render charts
      and write the AssistantQuery log row (see below).

    `job_tools_authorized` is honoured on both, but only the chat route
    should ever pass True (its own can_use_job_tools() result); the
    market-warehouse demo never passes it.

    Validates the question eagerly (raises AssistantInputError immediately,
    same as `answer()`) before returning the generator, so a bad question
    never opens an SSE stream at all. Everything past that point -- a
    missing/expired API key, a provider error, an unexpected exception --
    happens *inside* the generator and is yielded as an event instead of
    raising, since by then the HTTP response has already started streaming
    and can't switch to a different status code.

    Yields dicts with a "type" key:
      "retrieved"   -- {"type": "retrieved", "n_chunks": int}
      "tool_call"   -- {"type": "tool_call", "tool": str}       (model decided to call it)
      "tool_result" -- {"type": "tool_result", "tool": str, "arguments": dict, "result": str}
      "final"       -- {"type": "final", "reply": str}                 (detailed=False)
                       {"type": "final", "reply", "sources", "tool_trace",
                        "request_id", "backend", "model", "fell_back",
                        "prompt_tokens", "completion_tokens", "n_chunks",
                        "cache_hit", "guard_flagged", "guard_score",
                        "guard_error", "deadline_hit"}           (detailed=True)
      "busy"        -- {"type": "busy", "retry_after": int, "request_id": str}
                                                              (detailed=True only)
      "error"       -- {"type": "error", "message": str}
                       (+ "request_id" and "reason" when detailed=True;
                        "reason" is the internal cause, for the log only,
                        never for the visitor)

    A detailed cache hit or guard redirect yields only the "final" event
    (no "retrieved"): nothing was retrieved.
    """
    started = time.monotonic()
    q = _validate_question(question, config)
    initial_state = _prepare_initial_state(
        q,
        history,
        config=config,
        is_admin=is_admin,
        job_tools_authorized=job_tools_authorized,
        started=started,
    )
    if not detailed:
        return _stream_graph_events(initial_state)
    return _stream_detailed(
        q,
        history,
        initial_state,
        config=config,
        is_admin=is_admin,
        job_tools_authorized=job_tools_authorized,
    )


_STREAM_ERROR_TEXT = "Something went wrong on this turn."


def _final_event(answer_: AssistantAnswer) -> dict:
    return {
        "type": "final",
        "reply": answer_.reply,
        "sources": answer_.sources,
        "tool_trace": answer_.tool_trace,
        "request_id": answer_.request_id,
        "backend": answer_.backend,
        "model": answer_.model,
        "fell_back": answer_.fell_back,
        "prompt_tokens": answer_.prompt_tokens,
        "completion_tokens": answer_.completion_tokens,
        "n_chunks": answer_.n_chunks,
        "cache_hit": answer_.cache_hit,
        "guard_flagged": answer_.guard_flagged,
        "guard_score": answer_.guard_score,
        "guard_error": answer_.guard_error,
        "deadline_hit": answer_.deadline_hit,
    }


def _graph_progress(initial_state: _AssistantGraphState, state: dict):
    """Run the graph via `.stream()`, folding every node's update into
    `state` and yielding the progress events both stream contracts share
    (retrieved / tool_call / tool_result). Exceptions propagate to the
    caller, which decides how each kind is reported."""
    trace_emitted = 0
    for step in _ASSISTANT_GRAPH.stream(initial_state, stream_mode="updates"):
        for node_name, update in step.items():
            state.update(update)
            if node_name == "retrieve":
                yield {"type": "retrieved", "n_chunks": state.get("n_chunks", 0)}
            elif node_name == "agent":
                for tc in update.get("tool_calls") or ():
                    yield {"type": "tool_call", "tool": tc["name"]}
            elif node_name == "tools":
                full_trace = _extract_tool_trace(state.get("messages", []))
                for entry in full_trace[trace_emitted:]:
                    yield {"type": "tool_result", **entry}
                trace_emitted = len(full_trace)


def _stream_graph_events(initial_state: _AssistantGraphState):
    state: dict = dict(initial_state)
    try:
        yield from _graph_progress(initial_state, state)
    except AssistantUnavailable as exc:
        yield {"type": "error", "message": str(exc)}
        return
    except Exception:  # noqa: BLE001 - a viewer must never see a raw trace
        yield {"type": "error", "message": _STREAM_ERROR_TEXT}
        return

    yield {"type": "final", "reply": _finalize(state).reply}


def _stream_detailed(q, history, initial_state, *, config, is_admin, job_tools_authorized):
    request_id = _new_request_id()
    try:
        eligible = _cache_eligible(
            history, is_admin=is_admin, job_tools_authorized=job_tools_authorized
        )
        if eligible:
            hit = _from_cache(cache.lookup(q, config), request_id=request_id)
            if hit is not None:
                yield _final_event(hit)
                return

        verdict = _screen(_build_screen_text(q, history), config)
        if verdict.flagged:
            yield _final_event(_guard_redirect(verdict, request_id=request_id))
            return

        state: dict = dict(initial_state)
        yield from _graph_progress(initial_state, state)
        result = _finalize(state, request_id=request_id, verdict=verdict)
        if eligible and not state.get("exhausted"):
            _maybe_store(
                q, history, result, config=config, is_admin=is_admin,
                job_tools_authorized=job_tools_authorized,
            )
    except AssistantBusy as exc:
        yield {"type": "busy", "retry_after": busy_retry_after(exc), "request_id": request_id}
        return
    except AssistantUnavailable as exc:
        yield {
            "type": "error",
            "message": _STREAM_ERROR_TEXT,
            "reason": str(exc),
            "request_id": request_id,
        }
        return
    except Exception as exc:  # noqa: BLE001 - a viewer must never see a raw trace
        logger.exception("assistant stream turn failed (request_id=%s)", request_id)
        yield {
            "type": "error",
            "message": _STREAM_ERROR_TEXT,
            "reason": f"{type(exc).__name__}: {exc}",
            "request_id": request_id,
        }
        return

    yield _final_event(result)

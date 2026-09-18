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
Timed-Squares) are different: they wrap actions that are already public,
unauthenticated web features, so their schemas are built and offered on
every single call, regardless of `job_tools_authorized` -- there is no
gate to check. That means the tool-calling model path (the LangGraph
StateGraph built below and driven by `answer()`) is now always used;
there is no more plain single-call fallback.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TypedDict

from langgraph.graph import END, StateGraph

from .backends import build_backend
from .embeddings import build_embedder
from .errors import AssistantInputError, AssistantUnavailable
from .job_tools import build_job_tools, dispatch_job_tool
from .pipeline_tools import build_pipeline_tools, dispatch_pipeline_tool
from .prompts import build_messages
from .retrieval import retrieve
from .scorer_tools import build_scorer_tools, dispatch_scorer_tool
from .store import build_store
from .timedsquares_tools import build_timedsquares_tools, dispatch_timedsquares_tool
from .trading_tools import build_trading_tools, dispatch_trading_tool

_ALLOWED_ROLES = {"user", "assistant"}
_MAX_TURN_CHARS = 2000

# How many model<->tool round trips a single chat turn may take before we
# stop and return whatever text we have. Four covers "parse a pasted list,
# read it back, then write on confirmation" with headroom.
_MAX_TOOL_ITERS = 4


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


def _node_retrieve(state: _AssistantGraphState) -> dict:
    result = retrieve(
        state["question"],
        k=state["retrieval_k"],
        embedder=state["embedder"],
        store=state["store"],
    )
    messages = build_messages(
        question=state["question"],
        context_text=result.context_text,
        history=state["history"],
        is_admin=state["is_admin"],
        job_tools=state["job_tools_authorized"],
    )
    return {"messages": messages, "chunks": result.chunks, "n_chunks": len(result.chunks)}


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
    return {
        "messages": convo,
        "call_counts": call_counts,
        "tool_calls": None,
        "exhausted": state.get("iters", 0) >= state["max_iters"],
    }


def _route_after_tools(state: _AssistantGraphState) -> str:
    return END if state.get("exhausted") else "agent"


def _build_assistant_graph():
    builder = StateGraph(_AssistantGraphState)
    builder.add_node("retrieve", _node_retrieve)
    builder.add_node("agent", _node_agent)
    builder.add_node("tools", _node_tools)
    builder.set_entry_point("retrieve")
    builder.add_edge("retrieve", "agent")
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
) -> _AssistantGraphState:
    """Shared setup for `answer()` and `stream_answer()` -- everything that
    has to happen before the graph runs at all: history trimming, building
    the embedder/store, assembling the public tool set (and the job-tracker
    six when authorized) into one schema list plus one dispatch map, and
    the resulting initial graph state. Kept as one function so the two
    entry points can never drift on what tools/limits/config a turn runs
    with -- only how its progress is consumed (final value vs. streamed)."""
    history = _clean_history(history, int(config["ASSISTANT_MAX_HISTORY_TURNS"]))

    embedder = build_embedder(config)
    store = build_store(config)

    # The public tool sets are always built and offered -- no authorization
    # gate, unlike the job tracker. Build each domain's schemas once and
    # remember which dispatcher handles which tool name.
    trading_specs = build_trading_tools()
    pipeline_specs = build_pipeline_tools()
    scorer_specs = build_scorer_tools()
    timedsquares_specs = build_timedsquares_tools()

    dispatch_map = {}
    for spec in trading_specs:
        dispatch_map[spec["function"]["name"]] = dispatch_trading_tool
    for spec in pipeline_specs:
        dispatch_map[spec["function"]["name"]] = dispatch_pipeline_tool
    for spec in scorer_specs:
        dispatch_map[spec["function"]["name"]] = dispatch_scorer_tool
    for spec in timedsquares_specs:
        dispatch_map[spec["function"]["name"]] = dispatch_timedsquares_tool

    tools = trading_specs + pipeline_specs + scorer_specs + timedsquares_specs

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
    }


def _finalize(final_state: dict) -> AssistantAnswer:
    # Mirrors `_run_tool_loop`'s two exits exactly: a normal stop (no more
    # tool calls) hands back that last reply's text verbatim, even if it's
    # empty; running out of iterations still wanting tools hands back the
    # last non-empty text seen, or the fallback if there was none.
    if final_state.get("exhausted"):
        final_text = final_state.get("last_text") or _DEFAULT_TOOL_FALLBACK
    else:
        final_text = final_state.get("reply_text", "")

    sources = _pick_sources(final_state.get("chunks", []), final_text)
    tool_trace = _extract_tool_trace(final_state.get("messages", []))

    return AssistantAnswer(
        reply=(final_text or "").strip(),
        sources=sources,
        backend=final_state.get("backend_name", ""),
        model=final_state.get("model", ""),
        n_chunks=final_state.get("n_chunks", 0),
        prompt_tokens=final_state.get("prompt_tokens"),
        completion_tokens=final_state.get("completion_tokens"),
        tool_trace=tool_trace,
    )


def answer(
    question: str,
    history,
    *,
    config,
    is_admin: bool = False,
    job_tools_authorized: bool = False,
) -> AssistantAnswer:
    q = _validate_question(question, config)
    initial_state = _prepare_initial_state(
        q, history, config=config, is_admin=is_admin, job_tools_authorized=job_tools_authorized
    )
    final_state = _ASSISTANT_GRAPH.invoke(initial_state)
    return _finalize(final_state)


def stream_answer(question: str, history, *, config, is_admin: bool = False):
    """Same validation and setup as `answer()`, but yields progress events as
    the graph actually runs instead of blocking until it's done -- what the
    live "autonomous stock analysis" demo on the Market Data Warehouse page
    streams over SSE. Not used by the main /assistant chat endpoint, which
    stays on the plain `answer()` path above; job-tracker tools are never
    offered here (job_tools_authorized is not a parameter) since this is a
    public, unauthenticated demo entry point.

    Validates the question eagerly (raises AssistantInputError immediately,
    same as `answer()`) before returning the generator, so a bad question
    never opens an SSE stream at all. Everything past that point -- a
    missing/expired API key, a provider error, an unexpected exception --
    happens *inside* the generator and is yielded as an {"type": "error"}
    event instead of raising, since by then the HTTP response has already
    started streaming and can't switch to a different status code.

    Yields dicts with a "type" key:
      "retrieved"   -- {"type": "retrieved", "n_chunks": int}
      "tool_call"   -- {"type": "tool_call", "tool": str}       (model decided to call it)
      "tool_result" -- {"type": "tool_result", "tool": str, "arguments": dict, "result": str}
      "final"       -- {"type": "final", "reply": str}
      "error"       -- {"type": "error", "message": str}
    """
    q = _validate_question(question, config)
    initial_state = _prepare_initial_state(
        q, history, config=config, is_admin=is_admin, job_tools_authorized=False
    )
    return _stream_graph_events(initial_state)


def _stream_graph_events(initial_state: _AssistantGraphState):
    state: dict = dict(initial_state)
    trace_emitted = 0
    try:
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
    except AssistantUnavailable as exc:
        yield {"type": "error", "message": str(exc)}
        return
    except Exception:  # noqa: BLE001 - a viewer must never see a raw trace
        yield {"type": "error", "message": "Something went wrong on this turn."}
        return

    if state.get("exhausted"):
        final_text = state.get("last_text") or _DEFAULT_TOOL_FALLBACK
    else:
        final_text = state.get("reply_text", "")
    yield {"type": "final", "reply": (final_text or "").strip()}

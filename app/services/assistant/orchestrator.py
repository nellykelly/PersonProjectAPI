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
gate to check. That means the tool-calling model path (`_run_tool_loop`)
is now always used; there is no more plain single-call fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .backends import build_backend
from .embeddings import build_embedder
from .errors import AssistantInputError
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


def answer(
    question: str,
    history,
    *,
    config,
    is_admin: bool = False,
    job_tools_authorized: bool = False,
) -> AssistantAnswer:
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
    # always used -- there is no more plain single-call fallback.
    backend = build_backend(config, for_tools=True)
    messages = build_messages(
        question=q,
        context_text=result.context_text,
        history=history,
        is_admin=is_admin,
        job_tools=job_tools_authorized,
    )
    max_tokens = int(config["ASSISTANT_MAX_OUTPUT_TOKENS"])

    final_text, p_tok, c_tok, model = _run_tool_loop(
        backend,
        messages,
        tools,
        max_tokens,
        dispatch=_dispatch,
        tool_call_limits=_TOOL_CALL_LIMITS,
    )

    sources = _pick_sources(result.chunks, final_text)

    return AssistantAnswer(
        reply=(final_text or "").strip(),
        sources=sources,
        backend=backend.name,
        model=model,
        n_chunks=len(result.chunks),
        prompt_tokens=p_tok,
        completion_tokens=c_tok,
    )

"""The personal AI assistant: a dedicated page and two chat endpoints.

`GET /assistant` renders the chat page (or a calm "offline" panel when no
backend is configured). `POST /api/assistant/chat` runs one RAG turn and
answers with plain JSON. `POST /api/assistant/chat/stream` runs the same
turn but streams progress as Server-Sent Events while it happens -- see
`chat_stream()`'s docstring for the exact event contract sent to the
browser. Both endpoints share one rate-limit bucket (`_chat_rate_limit`
below) so a visitor can't double their quota by alternating between them.

Security posture:
- per-IP rate limit (shared across both endpoints), hard input-length cap,
  server-side history truncation
- the system prompt treats retrieved text and history as data, not
  instructions
- the job-tracker tools are handed to the model only when
  `can_use_job_tools()` holds -- the owner is signed in AND the
  /job-tracker gate is unlocked this session; for anyone else the tool
  schemas are never built, so there is nothing to escalate to
- NOT csrf-exempt: the fetch() sends an X-CSRFToken header
- every call is written to `assistant_queries` (question truncated, IP only
  ever stored hashed)
- any dependency failure -> a clean 503 with a friendly body, never a trace;
  every model in the chain rate-limited -> a 429 with Retry-After, distinct
  from the 503 (see `assistant.AssistantBusy`)
- a streamed turn's `tool_result` events (which can carry other visitors'
  text, e.g. a Pipeline World character's icebreaker answers) are never
  forwarded to the browser -- only a friendly "doing X" progress label is
"""
from __future__ import annotations

import hashlib
import json
import time

from flask import Response, current_app, jsonify, render_template, request, url_for

from app import RESUME_PATH
from app.blueprints.assistant import bp
from app.extensions import db, limiter
from app.models import AssistantQuery
from app.services import assistant, net_monitor
from app.services.assistant.authz import can_use_job_tools, is_owner
from app.services.assistant.orchestrator import busy_retry_after

_QUESTION_LOG_MAX = 500
_OFFLINE_MESSAGE = (
    "The assistant is unavailable right now. Please try again later, or email "
    "koskela.nelson@gmail.com."
)


def _busy_text(retry_after: int) -> str:
    # Plain, no em dashes (site copy convention) -- shared by the JSON
    # endpoint's 429 body and the streamed endpoint's "busy" event so the
    # two can't drift into two different tones for the same situation.
    return f"Hera's getting a lot of questions right now. Try again in about {retry_after} seconds."


# Friendly labels for the streamed "progress" events -- see chat_stream().
# Keyed by the exact tool name each *_tools.py module registers. An unknown
# name (a tool added later and not listed here yet) falls back to "Using a
# tool" rather than leaking the raw function name to a visitor.
_TOOL_PROGRESS_LABELS = {
    "get_quote": "Looking up a quote",
    "list_open_positions": "Checking open positions",
    "get_risk_report": "Running a risk report",
    "get_projection": "Running a price projection",
    "preview_open_position": "Previewing a trade",
    "open_position": "Opening a position",
    "preview_join_pipeline_world": "Previewing a Pipeline World character",
    "join_pipeline_world": "Joining Pipeline World",
    "check_character_status": "Checking a character's status",
    "score_company": "Scoring a company",
    "run_backtest": "Running a backtest",
    "get_leaderboard": "Checking the leaderboard",
    "get_traffic_summary": "Checking live site traffic",
    "add_application": "Updating the job tracker",
    "update_application": "Updating the job tracker",
    "set_application_status": "Updating the job tracker",
    "list_applications": "Checking the job tracker",
    "find_application": "Checking the job tracker",
    "ghost_stale_applications": "Updating the job tracker",
}
_DEFAULT_TOOL_PROGRESS_LABEL = "Using a tool"

# Where each content source actually lives on the site, so a citation in
# the chat can be a real link. Keys are ContentChunk.source values (the
# Markdown file paths under app/assistant_content/); a source with no
# entry here renders as plain text.
_SOURCE_ENDPOINTS = {
    "bio": "about.index",
    "faq": "about.index",
    "projects/trading-simulator": "trading.index",
    "projects/company-scorer": "qr.index",
    "projects/pipeline-world": "pipeline_world.index",
    "projects/sre-infra": "sre_infra.index",
    "projects/site-traffic": "sniffer.index",
    "projects/timed-squares": "timed_squares.index",
    "projects/beeznest": "projects.index",
    "projects/tiny-jvm": "tiny_jvm.index",
    "projects/market-warehouse": "market_warehouse.index",
    "projects/leetcode-150": "leetcode.index",
}


def _source_url(source: str) -> str | None:
    # The resume source cites the PDF itself, not the About page it's
    # linked from -- a visitor asking for the resume in chat should get
    # the file one click away, not a redirect through another page.
    if source == "resume":
        try:
            return url_for("static", filename=RESUME_PATH)
        except Exception:  # noqa: BLE001 - a missing endpoint just means no link
            return None
    endpoint = _SOURCE_ENDPOINTS.get(source)
    if not endpoint:
        return None
    try:
        return url_for(endpoint)
    except Exception:  # noqa: BLE001 - a missing endpoint just means no link
        return None


def _traffic_chart() -> dict:
    """Recomputed from the live buffer, the same "route re-derives the
    visual from the same service call the tool used, rather than piping
    raw numbers through the model's text result" pattern
    `market_warehouse.routes.api_analyze_stream`'s `_enrich()` uses for
    the projection chart -- a tool's string result is for the model, not
    a data channel to the frontend."""
    stats = net_monitor.get_analytics()
    buckets = stats["volume_buckets"]
    return {
        "kind": "series",
        "title": "Request volume over time",
        "labels": [b["start"].split("T")[-1].split(".")[0] for b in buckets],
        "series": [
            {"label": "Inbound", "data": [b["inbound"] for b in buckets]},
            {"label": "Outbound", "data": [b["outbound"] for b in buckets]},
        ],
    }


def _projection_chart(args: dict) -> dict | None:
    """Same re-derive-it-from-the-service pattern as `_traffic_chart()`,
    for the market-warehouse `get_projection` tool -- reuses the exact
    service call (`get_projection_chart_data`) and default-filling
    `market_warehouse.routes.api_analyze_stream`'s `_enrich()` already
    established for the SSE stock-analysis demo, so a chart asked for
    from the main chat and one streamed from that demo are drawn from
    the same numbers. Returns None when the ticker has no precomputed
    projection (an invalid/unsupported ticker, or a warehouse that isn't
    built) -- the model's own text reply already explains that case. Also
    returns None (never raises) on a malformed `args` shape -- a chart is a
    nice-to-have on top of a reply that already stands on its own, so a
    parsing hiccup here must never turn into a 500 for the whole turn."""
    if not isinstance(args, dict):
        return None
    from app.services import market_warehouse

    ticker = (args.get("ticker") or "").strip().upper()
    if not ticker:
        return None
    method = (args.get("method") or market_warehouse.DEFAULT_PROJECTION_METHOD).strip().lower()
    lookback = args.get("lookback_days") or market_warehouse.DEFAULT_PROJECTION_LOOKBACK
    try:
        lookback = int(lookback)
    except (TypeError, ValueError):
        lookback = market_warehouse.DEFAULT_PROJECTION_LOOKBACK

    chart_data = market_warehouse.get_projection_chart_data(
        current_app.config["MARKET_WAREHOUSE_DB_PATH"], method=method, lookback_days=lookback
    )
    proj = chart_data["projection"]["series"].get(ticker)
    if not proj or not proj.get("dates"):
        return None
    recent = chart_data["recent_actual"].get(ticker, {"dates": [], "close": []})

    return {
        "kind": "projection",
        "title": f"{ticker} price projection ({method}, {lookback}d lookback)",
        "ticker": ticker,
        "labels": recent["dates"] + proj["dates"],
        "actual": recent["close"],
        "projected": proj["projected"],
        "lower": proj["lower"],
        "upper": proj["upper"],
        "r_squared": proj.get("r_squared"),
    }


def _charts_for(tool_trace: list[dict]) -> list[dict]:
    """Which tool calls in this turn earn a chart in the chat UI, and
    what to draw. Keyed by tool name so adding another chart-worthy tool
    later is a one-line addition here, not a rewrite. A chart is always
    optional on top of a reply that already stands on its own without it,
    so any single call's failure here is swallowed, never a 500 for the
    whole turn."""
    charts = []
    for call in tool_trace:
        try:
            tool = call.get("tool")
            if tool == "get_traffic_summary":
                chart = _traffic_chart()
                if chart["labels"]:
                    charts.append(chart)
            elif tool == "get_projection":
                chart = _projection_chart(call.get("arguments") or {})
                if chart:
                    charts.append(chart)
        except Exception:  # noqa: BLE001 - a chart is optional; the reply text isn't
            current_app.logger.exception("assistant chat: chart enrichment failed")
    return charts


def _map_sources(sources: list[dict]) -> list[dict]:
    """AssistantAnswer.sources -> what the browser renders as citations.
    Shared by the JSON endpoint and the streamed endpoint's "final" event
    so the two never drift on shape."""
    return [
        {
            "title": s["title"],
            "label": s["title"].split(":", 1)[-1].strip() if ":" in s["title"] else s["title"],
            "source": s["source"],
            "url": _source_url(s["source"]),
        }
        for s in sources
    ]


def _ip_hash() -> str | None:
    ip = request.remote_addr
    if not ip:
        return None
    salt = current_app.config.get("SECRET_KEY", "")
    return hashlib.sha256(f"{ip}{salt}".encode("utf-8")).hexdigest()


# The owner check lives in app.services.assistant.authz now, so the route
# and the orchestrator's tool layer share one implementation. Kept under
# the old name because `_is_admin()` is what the logging path below reads.
_is_admin = is_owner


@bp.route("/assistant", methods=["GET"])
def index():
    return render_template(
        "assistant/index.html",
        assistant_online=assistant.backend_available(current_app.config),
    )


@bp.route("/assistant/about", methods=["GET"])
def about():
    """Static case-study page: what problem the assistant solves, what
    it's built with, and what makes it an autonomous agent rather than a
    plain chatbot. No model calls, no backend dependency -- it renders
    even when the assistant itself is offline."""
    return render_template("assistant/about.html")


@bp.route("/assistant/stats", methods=["GET"])
def stats():
    """Public, aggregate-only usage stats for the assistant. Built by a
    plain query over `assistant_queries` -- the per-row heuristic signals
    were written at chat time, so this page runs no model and costs no
    tokens (see app/services/assistant/analytics.py)."""
    from app.services.assistant.analytics import compute_stats

    return render_template("assistant/stats.html", stats=compute_stats(days=30))


# Shared between /api/assistant/chat and /api/assistant/chat/stream: one
# `scope` name means flask-limiter buckets both endpoints together, keyed
# by the same IP -- a visitor hitting the JSON endpoint 20 times has no
# separate 20-request allowance left on the streamed one. `shared_limit`
# (vs. two `limiter.limit()` calls with the same string) is what actually
# makes that one bucket instead of two identical-looking but independent
# ones; `override_defaults` (its default, True) applies here too.
_chat_rate_limit = limiter.shared_limit(
    lambda: current_app.config["ASSISTANT_CHAT_RATE_LIMIT"],
    scope="assistant_chat",
)


@bp.route("/api/assistant/chat", methods=["POST"])
@_chat_rate_limit
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get("message")
    history = data.get("history", [])
    is_admin = _is_admin()
    job_tools_ok = can_use_job_tools()

    started = time.monotonic()
    try:
        result = assistant.answer(
            message,
            history,
            config=current_app.config,
            is_admin=is_admin,
            job_tools_authorized=job_tools_ok,
        )
    except assistant.AssistantInputError as exc:
        return jsonify({"reply": str(exc), "error": True}), 400
    except assistant.AssistantBusy as exc:
        # Must be caught before AssistantUnavailable: AssistantBusy is a
        # subclass of it, so this except has to come first or it would
        # never be reached and every busy turn would look like a plain
        # 503 outage instead of "try again shortly".
        retry_after = busy_retry_after(exc)
        _log(
            question=message,
            is_admin=is_admin,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=str(exc),
            busy=True,
        )
        resp = jsonify(
            {
                "reply": _busy_text(retry_after),
                "error": True,
                "busy": True,
                "retry_after": retry_after,
            }
        )
        resp.headers["Retry-After"] = str(retry_after)
        return resp, 429
    except assistant.AssistantUnavailable as exc:
        _log(
            question=message,
            is_admin=is_admin,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=str(exc),
            busy=False,
        )
        return jsonify({"reply": _OFFLINE_MESSAGE, "error": True}), 503

    _log(
        question=message,
        is_admin=is_admin,
        latency_ms=int((time.monotonic() - started) * 1000),
        backend=result.backend,
        model=result.model,
        n_chunks=result.n_chunks,
        n_sources=len(result.sources),
        prompt_tokens_est=result.prompt_tokens,
        completion_tokens_est=result.completion_tokens,
        reply=result.reply,
        request_id=result.request_id,
        fell_back=result.fell_back,
        cache_hit=result.cache_hit,
        guard_flagged=result.guard_flagged,
        guard_score=result.guard_score,
        guard_error=result.guard_error,
        busy=False,
        deadline_hit=result.deadline_hit,
    )
    return jsonify(
        {
            "reply": result.reply,
            "sources": _map_sources(result.sources),
            "charts": _charts_for(result.tool_trace),
            "backend": result.backend,
            "error": False,
            "request_id": result.request_id,
        }
    )


@bp.route("/api/assistant/chat/stream", methods=["POST"])
@_chat_rate_limit
def chat_stream():
    """Same turn as `chat()`, streamed as Server-Sent Events while it runs
    instead of one blocking JSON response. Same rate-limit bucket, same
    CSRF enforcement (this route is not csrf-exempt either -- the fetch()
    in assistant.js sends the same X-CSRFToken header), same logging.

    `assistant.stream_answer(..., detailed=True)` is called *eagerly*,
    right here, before any Response is constructed: `stream_answer()`
    itself is a plain function, not a generator (see its docstring) -- it
    validates the question and builds the embedder/store/tool set
    synchronously and only *returns* a generator, so `AssistantInputError`
    (bad question) and `AssistantUnavailable` (the embedder or store
    failed to construct) both raise right here, before the SSE response
    ever opens, and get an ordinary 400/503 JSON body exactly like
    `chat()`'s. Once the generator itself starts running (inside `stream()`
    below), a failure can only be reported as an in-stream "error" event --
    the HTTP status and headers have already gone out by then. Guard,
    cache, and AssistantBusy are all handled *inside* that generator by
    `stream_answer`'s detailed path -- a busy turn never raises here, it
    yields a "busy" event instead (see orchestrator.stream_answer's
    docstring for the full event set it yields).

    Events sent to the browser (one JSON object per SSE `data:` frame):
      {"type": "progress", "message": str}
          A friendly one-line status -- "Searching the site", "Looking up
          a quote", etc. (see _TOOL_PROGRESS_LABELS). Zero or more of
          these, in order. A cache hit or a guard redirect sends none at
          all (nothing was retrieved or called).
      {"type": "final", "reply": str, "sources": [...], "charts": [...],
       "request_id": str}
          Exactly one of these ends every successful turn. `sources` and
          `charts` are shaped exactly like `chat()`'s JSON body.
      {"type": "busy", "retry_after": int, "message": str}
          Every model in the chain was rate-limited. No "final" follows.
      {"type": "error", "message": str}
          Anything else that went wrong. `message` is always the same
          friendly `_OFFLINE_MESSAGE` -- the orchestrator's internal
          "reason" (which can carry a raw provider error string) is logged
          server-side only and never put on the wire; see the module
          docstring's note on why `tool_result` events are held back for
          the same "never forward what a caller didn't clear for the
          browser" reason.

    A client disconnecting mid-stream raises `GeneratorExit` inside
    `stream()` at its current `yield` -- caught explicitly below so it
    propagates cleanly without falling into the generic `except Exception`
    branch and writing a second, bogus error log row on top of whatever
    (if anything) was already logged for this turn.
    """
    data = request.get_json(silent=True) or {}
    message = data.get("message")
    history = data.get("history", [])
    is_admin = _is_admin()
    job_tools_ok = can_use_job_tools()

    started = time.monotonic()
    try:
        events = assistant.stream_answer(
            message,
            history,
            config=current_app.config,
            is_admin=is_admin,
            job_tools_authorized=job_tools_ok,
            detailed=True,
        )
    except assistant.AssistantInputError as exc:
        return jsonify({"reply": str(exc), "error": True}), 400
    except assistant.AssistantUnavailable as exc:
        _log(
            question=message,
            is_admin=is_admin,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=str(exc),
            busy=False,
        )
        return jsonify({"reply": _OFFLINE_MESSAGE, "error": True}), 503

    # Captured now, while this request's context is still live -- by the
    # time Werkzeug actually iterates the generator below, this view has
    # already returned and current_app/db/request can't be resolved
    # anymore. Same pattern (and same reason) as
    # market_warehouse.routes.api_analyze_stream.
    app = current_app._get_current_object()
    environ = request.environ

    def _sse(payload: dict) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    def stream():
        with app.request_context(environ):
            logged = False
            try:
                for ev in events:
                    etype = ev.get("type")
                    if etype == "retrieved":
                        if ev.get("n_chunks"):
                            yield _sse({"type": "progress", "message": "Searching the site"})
                    elif etype == "tool_call":
                        label = _TOOL_PROGRESS_LABELS.get(
                            ev.get("tool"), _DEFAULT_TOOL_PROGRESS_LABEL
                        )
                        yield _sse({"type": "progress", "message": label})
                    elif etype == "tool_result":
                        # Never forwarded: a tool result can carry another
                        # visitor's own text (e.g. a Pipeline World
                        # character's icebreaker answers) -- see the
                        # module docstring.
                        continue
                    elif etype == "final":
                        sources = _map_sources(ev.get("sources") or [])
                        charts = _charts_for(ev.get("tool_trace") or [])
                        _log(
                            question=message,
                            is_admin=is_admin,
                            latency_ms=int((time.monotonic() - started) * 1000),
                            backend=ev.get("backend"),
                            model=ev.get("model"),
                            n_chunks=ev.get("n_chunks"),
                            n_sources=len(sources),
                            prompt_tokens_est=ev.get("prompt_tokens"),
                            completion_tokens_est=ev.get("completion_tokens"),
                            reply=ev.get("reply"),
                            request_id=ev.get("request_id"),
                            fell_back=ev.get("fell_back"),
                            cache_hit=ev.get("cache_hit"),
                            guard_flagged=ev.get("guard_flagged"),
                            guard_score=ev.get("guard_score"),
                            guard_error=ev.get("guard_error"),
                            busy=False,
                            deadline_hit=ev.get("deadline_hit"),
                        )
                        logged = True
                        yield _sse(
                            {
                                "type": "final",
                                "reply": ev.get("reply"),
                                "sources": sources,
                                "charts": charts,
                                "request_id": ev.get("request_id"),
                            }
                        )
                    elif etype == "busy":
                        retry_after = ev.get("retry_after") or 20
                        _log(
                            question=message,
                            is_admin=is_admin,
                            latency_ms=int((time.monotonic() - started) * 1000),
                            error="every model rate-limited",
                            request_id=ev.get("request_id"),
                            busy=True,
                        )
                        logged = True
                        yield _sse(
                            {
                                "type": "busy",
                                "retry_after": retry_after,
                                "message": _busy_text(retry_after),
                            }
                        )
                    elif etype == "error":
                        # ev["reason"] (the internal cause, sometimes a raw
                        # provider error string) is logged but never sent.
                        _log(
                            question=message,
                            is_admin=is_admin,
                            latency_ms=int((time.monotonic() - started) * 1000),
                            error=ev.get("reason") or ev.get("message"),
                            request_id=ev.get("request_id"),
                            busy=False,
                        )
                        logged = True
                        yield _sse({"type": "error", "message": _OFFLINE_MESSAGE})
            except GeneratorExit:
                # The client went away mid-stream. Nothing left to send,
                # and nothing new to log beyond whatever branch above
                # already logged (if any) -- just let the generator end.
                raise
            except Exception:  # noqa: BLE001 - a viewer must never see a raw trace
                current_app.logger.exception("assistant chat_stream: unhandled error mid-stream")
                if not logged:
                    _log(
                        question=message,
                        is_admin=is_admin,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        error="unhandled exception in chat_stream",
                        busy=False,
                    )
                yield _sse({"type": "error", "message": _OFFLINE_MESSAGE})

    response = Response(stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


_REPLY_LOG_MAX = 800
_GUARD_ERROR_LOG_MAX = 300


def _log(*, question, is_admin, latency_ms, backend=None, model=None, n_chunks=None,
         n_sources=None, prompt_tokens_est=None, completion_tokens_est=None,
         reply=None, error=None, request_id=None, fell_back=None, cache_hit=None,
         guard_flagged=None, guard_score=None, guard_error=None, busy=None,
         deadline_hit=None) -> None:
    from app.services.assistant.analytics import classify_message, classify_reply

    q = question if isinstance(question, str) else ""
    r = reply if isinstance(reply, str) else ""
    guard_error_trunc = guard_error[:_GUARD_ERROR_LOG_MAX] if isinstance(guard_error, str) else guard_error
    try:
        signals = classify_message(q)
        # A guard-flagged turn's "reply" is the canned redirect, not a real
        # answer -- classify_reply's text-based heuristics would otherwise
        # have to special-case that exact string, so the flag overrides
        # its verdict outright instead.
        reply_kind = "refused" if guard_flagged else classify_reply(r, error=bool(error))
        row = AssistantQuery(
            ip_hash=_ip_hash(),
            is_admin=is_admin,
            # "" (a cache hit or guard redirect never called a backend) is
            # logged as NULL, not as an empty string in an enumerable
            # String(16) column meant to hold 'groq' / 'fake' / ...
            backend=(backend or None),
            model=model,
            latency_ms=latency_ms,
            prompt_tokens_est=prompt_tokens_est,
            completion_tokens_est=completion_tokens_est,
            n_chunks=n_chunks,
            n_sources=n_sources,
            question=q[:_QUESTION_LOG_MAX] or None,
            reply=r[:_REPLY_LOG_MAX] or None,
            error=(error or None),
            word_count=signals["word_count"],
            sentiment=signals["sentiment"],
            is_frustrated=signals["is_frustrated"],
            profanity_count=signals["profanity_count"],
            category=signals["category"],
            reply_kind=reply_kind,
            request_id=request_id,
            fell_back=fell_back,
            cache_hit=cache_hit,
            guard_flagged=guard_flagged,
            guard_score=guard_score,
            guard_error=guard_error_trunc,
            busy=busy,
            deadline_hit=deadline_hit,
        )
        db.session.add(row)
        db.session.commit()
    except Exception:  # noqa: BLE001 - logging must never break the response
        db.session.rollback()

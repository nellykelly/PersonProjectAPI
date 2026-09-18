import datetime as dt
import json

from flask import Response, current_app, jsonify, render_template, request

from app.blueprints.market_warehouse import bp
from app.extensions import limiter
from app.services import assistant, market_warehouse, sse_limits
from app.services.assistant.trading_tools import PROJECTION_TICKERS

_SSE_CATEGORY = "stock-analysis"


def _stock_analysis_config(app_config) -> dict:
    """A shallow config copy with GROQ_API_KEY swapped to
    STOCK_ANALYSIS_GROQ_API_KEY (falling back to the shared assistant key
    when unset) -- a dedicated Groq account for this demo, not just a
    second key on the main one, since Groq's daily token cap is scoped to
    organization, not key (see STOCK_ANALYSIS_GROQ_API_KEY's config
    comment). Testing or traffic on this demo can then never eat into the
    real /assistant chat's budget again. build_backend()'s cache is keyed
    by (kind, model, api_key), so this and the main chat can safely name
    the same GROQ_TOOL_MODEL without colliding on one cached client."""
    config = dict(app_config)
    config["GROQ_API_KEY"] = app_config.get("STOCK_ANALYSIS_GROQ_API_KEY") or app_config.get("GROQ_API_KEY") or ""
    return config


@bp.route("")
def index():
    """A live read over the market-data-warehouse sibling project's
    DuckDB (or MotherDuck) file. See app/services/market_warehouse.py
    for why this never 500s: a missing file, a mid-refresh lock, or an
    unbuilt schema all render as a friendly "not available" message
    instead of an error page."""
    analytics = market_warehouse.get_analytics(current_app.config["MARKET_WAREHOUSE_DB_PATH"])
    return render_template("market_warehouse/index.html", analytics=analytics)


@bp.route("/api/chart")
def api_chart():
    """Backs the timeframe picker: GET ?start=YYYY-MM-DD&end=YYYY-MM-DD,
    either bound optional. Read-only, no state -- not CSRF-relevant."""
    start = request.args.get("start")
    end = request.args.get("end")
    for name, value in (("start", start), ("end", end)):
        if value is not None:
            try:
                dt.date.fromisoformat(value)
            except ValueError:
                return jsonify({"ok": False, "error": f"{name} must be YYYY-MM-DD"}), 400

    chart = market_warehouse.get_chart_window(
        current_app.config["MARKET_WAREHOUSE_DB_PATH"], start=start, end=end
    )
    return jsonify({"ok": True, "chart": chart})


@bp.route("/api/projection")
def api_projection():
    """NOT FINANCIAL ADVICE -- see app/services/market_warehouse.py.
    Backs the method/timeframe pickers on the projection chart:
    GET ?method=linear_trend&lookback=90. Read-only, no state."""
    method = request.args.get("method", market_warehouse.DEFAULT_PROJECTION_METHOD)
    lookback_raw = request.args.get("lookback", str(market_warehouse.DEFAULT_PROJECTION_LOOKBACK))

    if method not in market_warehouse.PROJECTION_METHODS:
        return jsonify({"ok": False, "error": "unknown method"}), 400
    try:
        lookback = int(lookback_raw)
    except ValueError:
        return jsonify({"ok": False, "error": "lookback must be an integer"}), 400
    if lookback not in market_warehouse.PROJECTION_LOOKBACK_GRID:
        return jsonify({"ok": False, "error": "unknown lookback"}), 400

    projection = market_warehouse.get_projection(
        current_app.config["MARKET_WAREHOUSE_DB_PATH"], method=method, lookback_days=lookback
    )
    return jsonify({"ok": True, "projection": projection})


@bp.route("/api/analyze/stream")
@limiter.limit(lambda: current_app.config["STOCK_ANALYSIS_RATE_LIMIT"])
def api_analyze_stream():
    """Server-Sent Events: a live, autonomous assistant turn focused on one
    ticker, streamed as it actually happens -- the same production
    /assistant LangGraph, driven via `stream_answer()` (LangGraph's own
    `.stream()`, not `.invoke()`) instead of blocking until the whole turn
    is done. The model decides for itself whether/which of get_quote,
    get_projection, and get_risk_report to call and in what order; this
    endpoint only nudges it toward one ticker and forwards each graph event
    to the page the instant it happens, so a visitor watches the real
    sequence of decisions instead of staring at "Thinking..." for however
    long a several-hop chain takes.

    GET (not POST) because EventSource can only issue GET and can't set a
    custom X-CSRFToken header -- fine here since CSRF protection only ever
    applies to state-changing methods, and this endpoint has no state to
    change. Rate limited the same as the old POST version (STOCK_ANALYSIS_RATE_LIMIT)
    since a stream still burns a real Groq quota, gated *before* the SSE
    response opens so a limited caller gets a normal 429 rather than a
    stream that immediately errors. Also takes an `sse_limits` concurrency
    slot like the Trading Simulator's risk feed and watchlist stream do --
    same shared-thread-pool reasoning, see app/services/sse_limits.py.
    """
    ticker = (request.args.get("ticker") or "").strip().upper()
    if ticker not in PROJECTION_TICKERS:
        return jsonify({
            "ok": False,
            "error": f"Pick one of the supported tickers: {', '.join(sorted(PROJECTION_TICKERS))}.",
        }), 400

    client_ip = request.remote_addr or "unknown"
    try:
        sse_limits.acquire_sse_slot(_SSE_CATEGORY, client_ip)
    except sse_limits.TooManyConnections as exc:
        return jsonify({"ok": False, "error": str(exc)}), 429

    question = (
        f"Give a full autonomous multi-step analysis of {ticker}: check its current "
        f"price, its experimental trend projection, and a risk report, using your "
        f"tools as needed, then briefly explain what each step told you. Only use "
        f"tools relevant to {ticker} -- nothing about other projects on this site."
    )

    # Captured now, while the request context is still live -- by the time
    # Werkzeug actually iterates the generator below, this view has already
    # returned and current_app/db can't be resolved anymore. Same reasoning
    # as the trading blueprint's two SSE routes -- except this stream goes
    # further than those two: it runs the full assistant graph, which can
    # reach trading tools (get_risk_report) that rate-limit via the
    # caller's IP (`flask_limiter`'s `get_remote_address()`, which reads
    # `request.remote_addr`). An app context alone restores `current_app`/
    # `db` but NOT `request` -- that needs a real request context, which is
    # exactly what a plain `with app.app_context():` here was missing,
    # surfacing as `RuntimeError: Working outside of request context`
    # buried inside dispatch_trading_tool's generic error text. Rebuilding
    # a request context from this same request's own WSGI environ (Flask's
    # documented pattern for "streaming with context") fixes it precisely
    # -- request.remote_addr resolves to the real caller, not a guess.
    app = current_app._get_current_object()
    environ = request.environ
    stock_analysis_config = _stock_analysis_config(app.config)

    def _enrich(event: dict) -> dict:
        # get_projection's tool result is a sentence for the MODEL to read
        # -- the same numbers this page's own "Price projection" chart
        # already draws are sitting right there in the warehouse, so pull
        # them again (by the exact method/lookback the model actually
        # called, not the page's default) and attach them as real chart
        # data instead of leaving the visual proof to a sentence.
        if event.get("type") != "tool_result" or event.get("tool") != "get_projection":
            return event
        args = event.get("arguments") or {}
        method = args.get("method") or market_warehouse.DEFAULT_PROJECTION_METHOD
        lookback = args.get("lookback_days") or market_warehouse.DEFAULT_PROJECTION_LOOKBACK
        try:
            lookback = int(lookback)
        except (TypeError, ValueError):
            lookback = market_warehouse.DEFAULT_PROJECTION_LOOKBACK
        chart_data = market_warehouse.get_projection_chart_data(
            app.config["MARKET_WAREHOUSE_DB_PATH"], method=method, lookback_days=lookback
        )
        if chart_data["projection"]["tickers"]:
            event = dict(event, chart=chart_data)
        return event

    def stream():
        with app.request_context(environ):
            try:
                yield ": connected\n\n"
                for event in assistant.stream_answer(question, [], config=stock_analysis_config):
                    yield f"data: {json.dumps(_enrich(event))}\n\n"
            except assistant.AssistantInputError as exc:
                yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
            finally:
                sse_limits.release_sse_slot(_SSE_CATEGORY, client_ip)

    response = Response(stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response

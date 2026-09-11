import json
import queue
import time

from flask import Response, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from app.blueprints.trading import bp
from app.extensions import db, limiter
from app.models import Leg, RiskRequest, Strategy, utcnow
from app.services import (
    instruments, market_data, pricing, risk_dashboard, risk_engine, risk_models, sse_limits, trading_actions,
    watchlist,
)
from app.services.assistant import rate_limit
from app.services.market_data import MarketDataError

SSE_KEEPALIVE_SECONDS = 15
RISK_FEED_INTERVAL_SECONDS = 10


def _session_id() -> str:
    return session.get("session_id") or "anonymous"


def _scenario_from_form(form) -> dict | None:
    """The what-if shock, shared by every risk-request route (leg,
    position, and book scope all accept the same two fields)."""
    spot_shock_raw = form.get("spot_shock_pct")
    vol_shock_raw = form.get("vol_shock_pts")
    if not spot_shock_raw and not vol_shock_raw:
        return None
    try:
        return {
            "spot_shock_pct": float(spot_shock_raw or 0),
            "vol_shock_pts": float(vol_shock_raw or 0),
        }
    except ValueError:
        raise ValueError("spot_shock_pct/vol_shock_pts must be numbers") from None


def _open_strategies_count(session_id: str) -> int:
    # The per-session cap is on booked *strategies* (what a visitor thinks
    # of as "a position I opened"), not legs -- a multi-leg strategy still
    # only counts once even though it holds several Leg rows.
    return Strategy.query.filter_by(session_id=session_id, status="open").count()


def _price_positions(legs: list[Leg]) -> list[dict]:
    quotes: dict[str, float | None] = {}
    rows = []
    for leg in legs:
        if leg.ticker not in quotes:
            try:
                quotes[leg.ticker] = market_data.get_last_price(leg.ticker)
            except MarketDataError:
                quotes[leg.ticker] = None

        underlying = quotes[leg.ticker]
        pnl = None
        greeks = None
        if underlying is not None:
            try:
                pnl = pricing.compute_pnl(
                    leg.kind,
                    leg.signed_quantity,
                    leg.entry_price,
                    underlying,
                    strike=leg.strike,
                    expiry=leg.expiry,
                    entry_iv=leg.entry_iv,
                )
            except Exception:
                pnl = None
            try:
                greeks = pricing.position_greeks(
                    leg.kind,
                    leg.signed_quantity,
                    underlying,
                    strike=leg.strike,
                    expiry=leg.expiry,
                    entry_iv=leg.entry_iv,
                )
            except Exception:
                greeks = None
        rows.append({"position": leg, "underlying_price": underlying, "pnl": pnl, "greeks": greeks})
    return rows


@bp.route("")
def index():
    open_legs = Leg.query.filter_by(status="open").order_by(Leg.opened_at.desc()).all()
    closed_legs = Leg.query.filter_by(status="closed").order_by(Leg.closed_at.desc()).limit(20).all()

    # The book overview above the instrument table is deliberately NOT a
    # fresh calculation on every page load -- it's whatever the last
    # whole-book risk request actually found, the same "a risk request
    # is a real persisted fact, not a number recomputed for this one
    # render" rule the rest of position -> risk request -> report
    # follows. No book-level request yet -> no overview, not a
    # silently-live number standing in for one.
    book_request = (
        RiskRequest.query.filter_by(scope="book", status="complete")
        .order_by(RiskRequest.id.desc())
        .first()
    )

    # The chart plots the book's PnL trend over time -- one point per
    # past whole-book request, oldest first -- rather than a per-instrument
    # breakdown of a single snapshot, since "how has the book been doing"
    # is the more useful question and there's already a real history of
    # persisted book-level requests to answer it from.
    book_history_requests = (
        RiskRequest.query.filter_by(scope="book", status="complete")
        .order_by(RiskRequest.id.desc())
        .limit(20)
        .all()
    )
    book_history = [
        {
            "date": req.requested_at.strftime("%Y-%m-%d %H:%M"),
            "pnl": (req.totals or {}).get("pnl"),
        }
        for req in reversed(book_history_requests)
    ]

    return render_template(
        "trading/index.html",
        rows=_price_positions(open_legs),
        closed_positions=closed_legs,
        whitelist=current_app.config["TICKER_WHITELIST"],
        max_open=current_app.config["TRADING_MAX_OPEN_POSITIONS_PER_SESSION"],
        open_count=_open_strategies_count(_session_id()),
        book_request=book_request,
        book_history=book_history,
    )


@bp.route("/open", methods=["POST"])
def open_position():
    # A plain call instead of @limiter.limit(...): this shares one bucket
    # (keyed by action name "trading_open_position") with Hera's
    # open_position tool via app.services.assistant.rate_limit.consume, so
    # asking the assistant to open a position draws from the same quota
    # the web form does rather than getting a second, unlimited one.
    if not rate_limit.consume("trading_open_position", current_app.config["TRADING_RATE_LIMIT"]):
        return jsonify({"error": "rate limited"}), 429

    session_id = _session_id()
    max_open = current_app.config["TRADING_MAX_OPEN_POSITIONS_PER_SESSION"]

    ticker = (request.form.get("ticker") or "").strip().upper()
    kind = (request.form.get("kind") or "stock").strip().lower()
    quantity = request.form.get("quantity", "1")
    strike = request.form.get("strike")
    expiry = request.form.get("expiry")

    try:
        leg = trading_actions.open_position(
            session_id, ticker, kind, quantity, strike=strike, expiry=expiry,
            max_open_positions=max_open,
        )
    except trading_actions.TradingActionError as exc:
        flash(str(exc), "error")
        return redirect(url_for("trading.index"))
    except MarketDataError as exc:
        flash(str(exc), "error")
        return redirect(url_for("trading.index"))

    flash(f"Opened a {kind} position on {ticker}.", "success")
    return redirect(url_for("trading.position_detail", position_id=leg.id))


@bp.route("/positions/<int:position_id>")
def position_detail(position_id):
    leg = db.get_or_404(Leg, position_id)
    priced = _price_positions([leg])[0] if leg.status == "open" else {
        "position": leg,
        "underlying_price": leg.close_price,
        "pnl": None,
        "greeks": None,
    }
    return render_template(
        "trading/position_detail.html", risk_model_options=risk_models.list_models(), **priced
    )


@bp.route("/positions/<int:position_id>/close", methods=["POST"])
@limiter.limit(lambda: current_app.config["TRADING_RATE_LIMIT"])
def close_position(position_id):
    leg = db.get_or_404(Leg, position_id)
    if leg.status == "closed":
        return redirect(url_for("trading.position_detail", position_id=leg.id))

    try:
        underlying_price = market_data.get_last_price(leg.ticker)
    except MarketDataError as exc:
        flash(str(exc), "error")
        return redirect(url_for("trading.position_detail", position_id=leg.id))

    result = pricing.compute_pnl(
        leg.kind,
        leg.signed_quantity,
        leg.entry_price,
        underlying_price,
        strike=leg.strike,
        expiry=leg.expiry,
        entry_iv=leg.entry_iv,
    )
    leg.status = "closed"
    leg.closed_at = utcnow()
    leg.close_price = result["current_value"]

    # Single-leg-per-strategy today, so closing the only leg always
    # closes the whole strategy too; a real multi-leg close would only
    # flip the strategy closed once every leg in it is closed.
    if leg.strategy.legs.filter(Leg.status == "open").count() == 0:
        leg.strategy.status = "closed"

    db.session.commit()

    flash(f"Closed {leg.ticker}: PnL ${result['pnl']:.2f}", "success")
    return redirect(url_for("trading.position_detail", position_id=leg.id))


@bp.route("/api/quote/<ticker>")
@limiter.limit(lambda: current_app.config["TRADING_READ_RATE_LIMIT"])
def api_quote(ticker):
    try:
        price = market_data.get_last_price(ticker)
        return jsonify({"ok": True, "ticker": ticker.upper(), "price": price})
    except MarketDataError as exc:
        return jsonify({"ok": False, "ticker": ticker.upper(), "error": str(exc)}), 503


@bp.route("/api/positions/<int:position_id>/history")
@limiter.limit(lambda: current_app.config["TRADING_READ_RATE_LIMIT"])
def api_position_history(position_id):
    leg = db.get_or_404(Leg, position_id)
    try:
        history = market_data.get_history(leg.ticker, period="6mo")
    except MarketDataError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    points = []
    for ts, row in history.iterrows():
        underlying = float(row["Close"])
        pnl_value = None
        try:
            pnl_value = pricing.compute_pnl(
                leg.kind,
                leg.signed_quantity,
                leg.entry_price,
                underlying,
                strike=leg.strike,
                expiry=leg.expiry,
                entry_iv=leg.entry_iv,
                as_of=ts.date(),
            )["pnl"]
        except Exception:
            pass
        points.append({"date": ts.strftime("%Y-%m-%d"), "price": round(underlying, 2), "pnl": pnl_value})

    return jsonify({"ok": True, "ticker": leg.ticker, "points": points})


@bp.route("/positions/<int:position_id>/risk-requests", methods=["POST"])
def submit_risk_request_route(position_id):
    """The "risk request" step: an explicit, queryable ask for risk on
    this leg -- either as-of-now (no body) or under a what-if scenario
    (spot_shock_pct / vol_shock_pts). Returns the persisted RiskRequest
    with its RiskResult already attached; GET /api/risk-requests/<id>
    fetches that same report again later.

    A plain call instead of @limiter.limit(...): this shares one bucket
    (keyed by action name "trading_risk_report") with Hera's
    get_risk_report tool via app.services.assistant.rate_limit.consume,
    across all three risk-request scopes (leg/position/book), so asking
    the assistant for a risk report draws from the same quota the web
    forms do rather than getting a separate, unlimited one."""
    if not rate_limit.consume("trading_risk_report", current_app.config["TRADING_RISK_REQUEST_RATE_LIMIT"]):
        return jsonify({"error": "rate limited"}), 429

    db.get_or_404(Leg, position_id)  # 404 before bothering to build a scenario

    try:
        scenario = _scenario_from_form(request.form)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    try:
        risk_request = risk_engine.submit_risk_request(
            leg_id=position_id, scenario=scenario, model_key=request.form.get("model_key")
        )
    except risk_models.UnknownModelError as exc:
        # A bad model name is the caller's mistake, not a server fault.
        return jsonify({"ok": False, "error": str(exc)}), 400
    except MarketDataError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    payload = risk_request.to_dict()
    payload["report_url"] = url_for("trading.risk_report", risk_request_id=risk_request.id)
    return jsonify({"ok": True, "risk_request": payload})


@bp.route("/strategies")
def strategies_index():
    """Every position in the shared book, newest first.

    Legs are priced through one shared quote map (see _price_positions),
    so a page listing many positions on the same underlying still makes a
    single market call per distinct ticker rather than one per leg."""
    strategies = trading_actions.list_book(limit=100)

    # Price every leg across every position in one pass, so the per-ticker
    # quote is fetched once for the whole page.
    all_legs = [leg for s in strategies for leg in s.legs]
    priced_by_leg = {row["position"].id: row for row in _price_positions(all_legs)}

    rows = []
    for strategy in strategies:
        legs = list(strategy.legs)
        net_pnl = 0.0
        priced_count = 0
        for leg in legs:
            priced = priced_by_leg.get(leg.id)
            if priced and priced["pnl"]:
                net_pnl += priced["pnl"]["pnl"]
                priced_count += 1
        rows.append(
            {
                "strategy": strategy,
                "leg_count": len(legs),
                "tickers": sorted({leg.ticker for leg in legs}),
                # None rather than 0 when nothing could be priced, so the
                # template can say "n/a" instead of implying a flat book.
                "net_pnl": net_pnl if priced_count else None,
                "open_legs": sum(1 for leg in legs if leg.status == "open"),
            }
        )

    return render_template(
        "trading/strategies_index.html", rows=rows, risk_model_options=risk_models.list_models()
    )


@bp.route("/strategies/<int:strategy_id>")
def strategy_detail(strategy_id):
    """A position as its own entity: what it holds, what each leg is
    doing, and every risk request ever run against it."""
    strategy = db.get_or_404(Strategy, strategy_id)
    legs = list(strategy.legs)
    requests = (
        RiskRequest.query.filter_by(strategy_id=strategy_id)
        .order_by(RiskRequest.id.desc())
        .limit(25)
        .all()
    )
    return render_template(
        "trading/strategy_detail.html",
        strategy=strategy,
        rows=_price_positions(legs),
        risk_requests=requests,
        risk_model_options=risk_models.list_models(),
    )


@bp.route("/api/strategies/<int:strategy_id>")
def api_strategy(strategy_id):
    """The position as queryable JSON: its legs and its risk history."""
    strategy = db.get_or_404(Strategy, strategy_id)
    requests = (
        RiskRequest.query.filter_by(strategy_id=strategy_id)
        .order_by(RiskRequest.id.desc())
        .limit(25)
        .all()
    )
    return jsonify(
        {
            "ok": True,
            "position": strategy.to_dict(),
            "risk_requests": [r.to_dict() for r in requests],
        }
    )


@bp.route("/strategies/<int:strategy_id>/risk-requests", methods=["POST"])
def submit_position_risk_request(strategy_id):
    """Runs one risk request across every leg in the position, all priced
    off a single market snapshot, and returns the persisted request with
    its aggregated totals. Shares the "trading_risk_report" rate-limit
    bucket with the leg- and book-scope risk-request routes and with
    Hera's get_risk_report tool -- see submit_risk_request_route above."""
    if not rate_limit.consume("trading_risk_report", current_app.config["TRADING_RISK_REQUEST_RATE_LIMIT"]):
        return jsonify({"error": "rate limited"}), 429

    db.get_or_404(Strategy, strategy_id)

    try:
        scenario = _scenario_from_form(request.form)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    try:
        risk_request = risk_engine.submit_risk_request(
            strategy_id=strategy_id, scenario=scenario, model_key=request.form.get("model_key")
        )
    except risk_models.UnknownModelError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except ValueError as exc:
        # e.g. a position with no legs to price.
        return jsonify({"ok": False, "error": str(exc)}), 400
    except MarketDataError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    payload = risk_request.to_dict()
    payload["report_url"] = url_for("trading.risk_report", risk_request_id=risk_request.id)
    return jsonify({"ok": True, "risk_request": payload})


@bp.route("/risk-requests", methods=["POST"])
def submit_book_risk_request():
    """Runs one risk request across every open leg in the whole book --
    the "run risk on this report" action from the all-positions page.
    Every instrument is priced off one shared market snapshot, exactly
    like a position-level run, just scoped to everything at once instead
    of one Strategy. Shares the "trading_risk_report" rate-limit bucket
    with the leg- and position-scope risk-request routes and with Hera's
    get_risk_report tool -- see submit_risk_request_route above."""
    if not rate_limit.consume("trading_risk_report", current_app.config["TRADING_RISK_REQUEST_RATE_LIMIT"]):
        return jsonify({"error": "rate limited"}), 429

    try:
        scenario = _scenario_from_form(request.form)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    try:
        risk_request = risk_engine.submit_risk_request(
            book=True, scenario=scenario, model_key=request.form.get("model_key")
        )
    except risk_models.UnknownModelError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except ValueError as exc:
        # e.g. no open legs anywhere in the book to price.
        return jsonify({"ok": False, "error": str(exc)}), 400
    except MarketDataError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    payload = risk_request.to_dict()
    payload["report_url"] = url_for("trading.risk_report", risk_request_id=risk_request.id)
    return jsonify({"ok": True, "risk_request": payload})


@bp.route("/instruments")
def instruments_index():
    """The instrument catalog: every distinct contract ever booked,
    lookupable by its OCC-style code or underlying ticker."""
    query = request.args.get("q", "").strip()
    results = instruments.search_instruments(query)
    leg_counts = dict(
        db.session.query(Leg.instrument_id, db.func.count(Leg.id))
        .filter(Leg.instrument_id.in_([i.id for i in results]))
        .group_by(Leg.instrument_id)
        .all()
    ) if results else {}
    return render_template(
        "trading/instruments_index.html",
        instruments=results,
        leg_counts=leg_counts,
        query=query,
    )


@bp.route("/instruments/<code>")
def instrument_detail(code):
    """One instrument's reference data plus every leg (across every
    position, anyone's) that has ever traded it -- the point of keeping
    one master row per contract instead of each trade embedding its own
    copy."""
    instrument = instruments.find_instrument(code)
    if instrument is None:
        abort(404)
    legs = (
        Leg.query.filter_by(instrument_id=instrument.id)
        .order_by(Leg.id.desc())
        .all()
    )
    return render_template("trading/instrument_detail.html", instrument=instrument, legs=legs)


@bp.route("/api/instruments/<code>")
def api_instrument(code):
    instrument = instruments.find_instrument(code)
    if instrument is None:
        return jsonify({"ok": False, "error": f"no instrument with code {code!r}"}), 404
    legs = Leg.query.filter_by(instrument_id=instrument.id).order_by(Leg.id.desc()).all()
    return jsonify(
        {
            "ok": True,
            "instrument": instrument.to_dict(),
            "legs": [{"id": leg.id, "strategy_id": leg.strategy_id, "status": leg.status} for leg in legs],
        }
    )


@bp.route("/risk-reports/<int:risk_request_id>")
def risk_report(risk_request_id):
    """The report a risk request produces, on its own page.

    Generated per request rather than stored: the RiskRequest and its
    RiskResult are the durable record, and this renders them. That means
    an old report can always be reopened at a stable URL and will show
    exactly the numbers that run produced, including which model answered
    and what inputs it saw."""
    risk_request = db.get_or_404(RiskRequest, risk_request_id)
    results = risk_request.results.all()
    # The single-result detail block is only meaningful for a one-leg run;
    # a multi-leg report leads with the totals and per-leg table instead.
    result = results[0] if results else None
    try:
        model = risk_models.get_model(risk_request.model_key)
    except risk_models.UnknownModelError:
        # A model that has since been removed from the registry shouldn't
        # make its historical reports unopenable.
        model = None
    return render_template(
        "trading/risk_report.html",
        risk_request=risk_request,
        result=result,
        results=results,
        model=model,
        # None for a position-level run, which the template handles.
        leg=risk_request.leg,
    )


@bp.route("/api/risk-requests/<int:risk_request_id>")
def api_risk_request_report(risk_request_id):
    """The "report" step: fetch a previously-submitted risk request's
    result again, without recomputing anything -- it was already
    persisted the moment it ran."""
    risk_request = db.get_or_404(RiskRequest, risk_request_id)
    return jsonify({"ok": True, "risk_request": risk_request.to_dict()})


@bp.route("/risk-dashboard")
def risk_dashboard_view():
    """The site-wide "report" view over the whole position -> risk
    request -> report/live feed system: every risk request run against
    any leg, not just the per-leg history panel on one position's own
    detail page (see app/services/risk_dashboard.py)."""
    return render_template(
        "trading/risk_dashboard.html",
        stats=risk_dashboard.summary_stats(),
        recent=risk_dashboard.recent_risk_requests(),
    )


@bp.route("/api/positions/<int:position_id>/risk-feed")
def api_risk_feed(position_id):
    """Server-Sent Events: the "live data feed" step -- submits a fresh
    RiskRequest on an interval for as long as a viewer is connected,
    streaming each RiskResult the instant it's computed. Each tick is a
    real, persisted RiskRequest/RiskResult row, not a throwaway
    calculation, so a live-feed session leaves behind the same queryable
    history a single on-demand request would (see risk_engine.py).

    Captured here, while the request context is still active -- by the
    time the generator body below actually runs (Werkzeug iterates it
    lazily, after this view function has already returned), the context
    is gone and current_app/db can't be resolved anymore (same reasoning
    as the watchlist SSE route above)."""
    db.get_or_404(Leg, position_id)
    client_ip = request.remote_addr or "unknown"
    try:
        sse_limits.acquire_sse_slot("risk-feed", client_ip)
    except sse_limits.TooManyConnections as exc:
        return jsonify({"ok": False, "error": str(exc)}), 429

    app = current_app._get_current_object()

    def stream():
        with app.app_context():
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        risk_request = risk_engine.submit_risk_request(leg_id=position_id)
                        payload = risk_request.to_dict()
                    except Exception as exc:
                        payload = {"error": str(exc)}
                    yield f"data: {json.dumps(payload)}\n\n"
                    time.sleep(RISK_FEED_INTERVAL_SECONDS)
            finally:
                sse_limits.release_sse_slot("risk-feed", client_ip)

    response = Response(stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@bp.route("/api/expiries/<ticker>")
@limiter.limit(lambda: current_app.config["TRADING_READ_RATE_LIMIT"])
def api_expiries(ticker):
    try:
        return jsonify({"ok": True, "expiries": market_data.get_expiries(ticker)})
    except MarketDataError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


@bp.route("/api/chain/<ticker>/<expiry>")
@limiter.limit(lambda: current_app.config["TRADING_READ_RATE_LIMIT"])
def api_chain(ticker, expiry):
    try:
        chain = market_data.get_option_chain(ticker, expiry)
        return jsonify({"ok": True, **chain})
    except MarketDataError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


@bp.route("/watchlist")
def watchlist_view():
    return render_template(
        "trading/watchlist.html",
        tickers=current_app.config["TICKER_WHITELIST"],
        snapshot=watchlist.get_snapshot(),
        market_open=watchlist.is_market_open(),
    )


@bp.route("/api/watchlist/stream")
def api_watchlist_stream():
    """Server-Sent Events: pushes each ticker's refreshed price the
    instant the background poller fetches it (see
    app/services/watchlist.py). The poller only runs while at least one
    connection like this one is open and only during market hours --
    this route starting it is what "at least one viewer" means."""

    client_ip = request.remote_addr or "unknown"
    try:
        sse_limits.acquire_sse_slot("watchlist", client_ip)
    except sse_limits.TooManyConnections as exc:
        return jsonify({"ok": False, "error": str(exc)}), 429

    # Captured here, while the request context is still active -- by the
    # time the generator body below actually runs (Werkzeug iterates it
    # lazily, after this view function has already returned), the
    # context is gone and current_app can't be resolved anymore.
    app = current_app._get_current_object()

    def stream():
        with app.app_context():
            q = watchlist.subscribe()
            watchlist.ensure_poller_running(app)
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        entry = q.get(timeout=SSE_KEEPALIVE_SECONDS)
                        yield f"data: {json.dumps(entry)}\n\n"
                    except queue.Empty:
                        yield ": keepalive\n\n"
            finally:
                watchlist.unsubscribe(q)
                sse_limits.release_sse_slot("watchlist", client_ip)

    response = Response(stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response

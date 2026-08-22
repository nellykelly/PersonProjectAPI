import json
import queue
from datetime import datetime

from flask import Response, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from app.blueprints.trading import bp
from app.extensions import db, limiter
from app.models import Position, utcnow
from app.services import market_data, pricing, watchlist
from app.services.market_data import MarketDataError

SSE_KEEPALIVE_SECONDS = 15


def _session_id() -> str:
    return session.get("session_id") or "anonymous"


def _open_positions_count(session_id: str) -> int:
    return Position.query.filter_by(session_id=session_id, status="open").count()


def _price_positions(positions: list[Position]) -> list[dict]:
    quotes: dict[str, float | None] = {}
    rows = []
    for position in positions:
        if position.ticker not in quotes:
            try:
                quotes[position.ticker] = market_data.get_last_price(position.ticker)
            except MarketDataError:
                quotes[position.ticker] = None

        underlying = quotes[position.ticker]
        pnl = None
        if underlying is not None:
            try:
                pnl = pricing.compute_pnl(
                    position.kind,
                    position.quantity,
                    position.entry_price,
                    underlying,
                    strike=position.strike,
                    expiry=position.expiry,
                    entry_iv=position.entry_iv,
                )
            except Exception:
                pnl = None
        rows.append({"position": position, "underlying_price": underlying, "pnl": pnl})
    return rows


@bp.route("")
def index():
    open_positions = Position.query.filter_by(status="open").order_by(Position.opened_at.desc()).all()
    closed_positions = (
        Position.query.filter_by(status="closed").order_by(Position.closed_at.desc()).limit(20).all()
    )

    return render_template(
        "trading/index.html",
        rows=_price_positions(open_positions),
        closed_positions=closed_positions,
        whitelist=current_app.config["TICKER_WHITELIST"],
        max_open=current_app.config["TRADING_MAX_OPEN_POSITIONS_PER_SESSION"],
        open_count=_open_positions_count(_session_id()),
    )


@bp.route("/open", methods=["POST"])
@limiter.limit(lambda: current_app.config["TRADING_RATE_LIMIT"])
def open_position():
    session_id = _session_id()
    max_open = current_app.config["TRADING_MAX_OPEN_POSITIONS_PER_SESSION"]
    if _open_positions_count(session_id) >= max_open:
        flash(f"You've reached the max of {max_open} open positions for this session.", "error")
        return redirect(url_for("trading.index"))

    ticker = (request.form.get("ticker") or "").strip().upper()
    kind = (request.form.get("kind") or "stock").strip().lower()

    if kind not in ("stock", "call", "put"):
        flash("Invalid position type.", "error")
        return redirect(url_for("trading.index"))

    try:
        quantity = int(request.form.get("quantity", "1"))
        if not (0 < quantity <= 1000):
            raise ValueError
    except (TypeError, ValueError):
        flash("Quantity must be a whole number between 1 and 1000.", "error")
        return redirect(url_for("trading.index"))

    if not market_data.is_valid_ticker(ticker):
        flash(f"'{ticker}' is not on the supported ticker list for this demo.", "error")
        return redirect(url_for("trading.index"))

    try:
        underlying_price = market_data.get_last_price(ticker)
    except MarketDataError as exc:
        flash(str(exc), "error")
        return redirect(url_for("trading.index"))

    position = Position(
        session_id=session_id,
        ticker=ticker,
        kind=kind,
        quantity=quantity,
        entry_underlying_price=underlying_price,
    )

    if kind == "stock":
        position.entry_price = underlying_price
    else:
        expiry_str = request.form.get("expiry")
        strike_raw = request.form.get("strike")
        if not expiry_str or not strike_raw:
            flash("Options require an expiry date and a strike.", "error")
            return redirect(url_for("trading.index"))
        try:
            expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
            strike = float(strike_raw)
        except ValueError:
            flash("Invalid expiry or strike.", "error")
            return redirect(url_for("trading.index"))

        try:
            chain = market_data.get_option_chain(ticker, expiry_str)
        except MarketDataError as exc:
            flash(str(exc), "error")
            return redirect(url_for("trading.index"))

        side = chain["calls"] if kind == "call" else chain["puts"]
        match = min(side, key=lambda o: abs(o["strike"] - strike)) if side else None
        if match is None:
            flash("No matching contract found for that expiry/strike.", "error")
            return redirect(url_for("trading.index"))

        position.strike = match["strike"]
        position.expiry = expiry
        position.entry_price = match.get("lastPrice") or 0.0
        position.entry_iv = match.get("impliedVolatility")

    db.session.add(position)
    db.session.commit()
    flash(f"Opened a {kind} position on {ticker}.", "success")
    return redirect(url_for("trading.position_detail", position_id=position.id))


@bp.route("/positions/<int:position_id>")
def position_detail(position_id):
    position = db.get_or_404(Position, position_id)
    priced = _price_positions([position])[0] if position.status == "open" else {
        "position": position,
        "underlying_price": position.close_price,
        "pnl": None,
    }
    return render_template("trading/position_detail.html", **priced)


@bp.route("/positions/<int:position_id>/close", methods=["POST"])
def close_position(position_id):
    position = db.get_or_404(Position, position_id)
    if position.status == "closed":
        return redirect(url_for("trading.position_detail", position_id=position.id))

    try:
        underlying_price = market_data.get_last_price(position.ticker)
    except MarketDataError as exc:
        flash(str(exc), "error")
        return redirect(url_for("trading.position_detail", position_id=position.id))

    result = pricing.compute_pnl(
        position.kind,
        position.quantity,
        position.entry_price,
        underlying_price,
        strike=position.strike,
        expiry=position.expiry,
        entry_iv=position.entry_iv,
    )
    position.status = "closed"
    position.closed_at = utcnow()
    position.close_price = result["current_value"]
    db.session.commit()

    flash(f"Closed {position.ticker}: PnL ${result['pnl']:.2f}", "success")
    return redirect(url_for("trading.position_detail", position_id=position.id))


@bp.route("/api/quote/<ticker>")
def api_quote(ticker):
    try:
        price = market_data.get_last_price(ticker)
        return jsonify({"ok": True, "ticker": ticker.upper(), "price": price})
    except MarketDataError as exc:
        return jsonify({"ok": False, "ticker": ticker.upper(), "error": str(exc)}), 503


@bp.route("/api/positions/<int:position_id>/history")
def api_position_history(position_id):
    position = db.get_or_404(Position, position_id)
    try:
        history = market_data.get_history(position.ticker, period="6mo")
    except MarketDataError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    points = []
    for ts, row in history.iterrows():
        underlying = float(row["Close"])
        pnl_value = None
        try:
            pnl_value = pricing.compute_pnl(
                position.kind,
                position.quantity,
                position.entry_price,
                underlying,
                strike=position.strike,
                expiry=position.expiry,
                entry_iv=position.entry_iv,
                as_of=ts.date(),
            )["pnl"]
        except Exception:
            pass
        points.append({"date": ts.strftime("%Y-%m-%d"), "price": round(underlying, 2), "pnl": pnl_value})

    return jsonify({"ok": True, "ticker": position.ticker, "points": points})


@bp.route("/api/expiries/<ticker>")
def api_expiries(ticker):
    try:
        return jsonify({"ok": True, "expiries": market_data.get_expiries(ticker)})
    except MarketDataError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503


@bp.route("/api/chain/<ticker>/<expiry>")
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

    # Captured here, while the request context is still active -- by the
    # time the generator body below actually runs (Werkzeug iterates it
    # lazily, after this view function has already returned), the
    # context is gone and current_app can't be resolved anymore.
    app = current_app._get_current_object()

    def stream():
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

    response = Response(stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response

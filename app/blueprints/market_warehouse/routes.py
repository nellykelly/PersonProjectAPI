import datetime as dt

from flask import current_app, jsonify, render_template, request

from app.blueprints.market_warehouse import bp
from app.services import market_warehouse


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

from flask import current_app, jsonify, render_template, request

from app.blueprints.qr import bp
from app.services import backtest, edgar, market_data, quant_score
from app.services.assistant import rate_limit


def _build_categories(report: dict) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for key, spec in report["metric_specs"].items():
        grouped.setdefault(spec["category"], []).append(
            {
                "key": key,
                "label": spec["label"],
                "is_pct": key in quant_score.PCT_METRICS,
                "raw": report["raw_metrics"].get(key),
                "score": report["metric_scores"].get(key),
            }
        )

    categories = []
    for name in quant_score.CATEGORIES:
        categories.append(
            {
                "name": name,
                "score": report["category_scores"].get(name),
                "weight": report["weights_used"].get(name),
                "configured_weight": report["weights_configured"].get(name),
                "metrics": grouped.get(name, []),
            }
        )
    return categories


@bp.route("")
def index():
    if not rate_limit.consume("qr_score", current_app.config["QR_SCORE_RATE_LIMIT"]):
        return jsonify(error="rate limited"), 429

    ticker = (request.args.get("ticker") or "").strip().upper()
    report = None
    categories = None
    error = None

    if ticker:
        if not market_data.is_valid_ticker(ticker):
            error = f"'{ticker}' is not on the supported ticker list for this demo."
        else:
            try:
                report = quant_score.score_company(ticker)
                categories = _build_categories(report)
            except (market_data.MarketDataError, edgar.EdgarError) as exc:
                error = str(exc)

    return render_template(
        "qr/index.html",
        whitelist=current_app.config["TICKER_WHITELIST"],
        weights=current_app.config["QR_WEIGHTS"],
        ticker=ticker,
        report=report,
        categories=categories,
        error=error,
    )


@bp.route("/backtest")
def backtest_view():
    if not rate_limit.consume("qr_backtest", current_app.config["QR_BACKTEST_RATE_LIMIT"]):
        return jsonify(error="rate limited"), 429

    years_ago = request.args.get("years_ago", "1")
    try:
        years_ago = int(years_ago)
    except ValueError:
        years_ago = 1
    years_ago = max(1, min(years_ago, 5))

    result = backtest.run_backtest(years_ago=years_ago)
    return render_template("qr/backtest.html", result=result, years_ago=years_ago)

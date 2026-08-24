"""Company Scorer backtest: the "quant calculation on validation" piece.

For each ticker in a basket, scores the company using only fundamentals
that were *actually public* ~1 year ago (edgar.get_fundamentals filters
by filing date, not period-end, so this avoids look-ahead bias), then
compares that historical score to the real forward price return since
then. Reports a per-ticker table plus a Pearson correlation between
score and forward return across the basket -- i.e. "did a higher score
actually predict better subsequent performance."

This is a demo-scale validation (a handful of tickers, one lookback
window), not a rigorous research backtest -- documented as such in the
QR project README.
"""
from __future__ import annotations

import math
import time
from datetime import date, timedelta

from flask import current_app

from app.services import market_data, quant_score

_cache: dict[tuple, dict] = {}


def _pearson_correlation(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return None
    return round(cov / math.sqrt(var_x * var_y), 3)


def run_backtest(tickers: list[str] | None = None, years_ago: int = 1, use_cache: bool = True) -> dict:
    tickers = tuple(tickers or current_app.config["QR_BACKTEST_TICKERS"])
    cache_key = (tickers, years_ago)

    if use_cache and cache_key in _cache:
        entry = _cache[cache_key]
        ttl = current_app.config["QR_BACKTEST_CACHE_TTL_SECONDS"]
        if time.time() - entry["_cached_at"] < ttl:
            return entry["result"]

    as_of_date = date.today() - timedelta(days=365 * years_ago)
    rows = []
    for ticker in tickers:
        try:
            report = quant_score.score_company(ticker, as_of_date=as_of_date)
            score = report["overall_score"]
            if score is None:
                rows.append({"ticker": ticker, "error": "insufficient data to compute a score"})
                continue

            price_then = market_data.get_price_near_date(ticker, as_of_date)
            price_now = market_data.get_last_price(ticker)
            forward_return_pct = round((price_now - price_then) / price_then * 100, 2)

            rows.append(
                {
                    "ticker": ticker,
                    "score": score,
                    "price_then": round(price_then, 2),
                    "price_now": round(price_now, 2),
                    "forward_return_pct": forward_return_pct,
                }
            )
        except Exception as exc:  # noqa: BLE001 - surfaced per-row, not fatal to the whole basket
            rows.append({"ticker": ticker, "error": str(exc)})

    valid = [r for r in rows if "error" not in r]
    correlation = _pearson_correlation(
        [r["score"] for r in valid], [r["forward_return_pct"] for r in valid]
    )

    result = {
        "as_of_date": as_of_date.isoformat(),
        "years_ago": years_ago,
        "rows": rows,
        "n_valid": len(valid),
        "n_total": len(tickers),
        "correlation": correlation,
    }

    _cache[cache_key] = {"result": result, "_cached_at": time.time()}
    return result

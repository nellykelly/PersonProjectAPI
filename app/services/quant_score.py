"""Company Scorer: composite multi-factor scoring engine.

Combines SEC EDGAR fundamentals (edgar.py) with yfinance market data
(market_data.py) into four category sub-scores -- valuation, leverage,
growth, profitability -- exactly as specified, each normalized 0-100,
then rolled into a single overall score using **configurable** weights
(app.config["QR_WEIGHTS"], equal-weight by default -- see config.py).

When a specific metric can't be computed for a company (a missing XBRL
tag is common in practice), it's simply dropped and the surrounding
category/overall weights renormalize over whatever *is* available,
rather than erroring out or silently treating missing data as zero.
"""
from __future__ import annotations

from datetime import date

from flask import current_app

from app.services import edgar, market_data

# metric -> (category, direction, low, high) used to linearly normalize
# a raw ratio into a 0-100 sub-score. Bounds are deliberately simple,
# broad heuristics (not sector-tuned) -- documented as a v1 choice.
METRIC_SPECS: dict[str, dict] = {
    "pe": {"label": "P/E", "category": "valuation", "direction": "lower_better", "lo": 5, "hi": 40},
    "pb": {"label": "P/B", "category": "valuation", "direction": "lower_better", "lo": 0.5, "hi": 10},
    "ev_ebitda": {"label": "EV/EBITDA", "category": "valuation", "direction": "lower_better", "lo": 5, "hi": 25},
    "debt_to_equity": {"label": "Debt/Equity", "category": "leverage", "direction": "lower_better", "lo": 0, "hi": 3},
    "current_ratio": {"label": "Current Ratio", "category": "leverage", "direction": "higher_better", "lo": 0.5, "hi": 3},
    "interest_coverage": {"label": "Interest Coverage", "category": "leverage", "direction": "higher_better", "lo": 0, "hi": 15},
    "revenue_growth": {"label": "Revenue Growth (YoY)", "category": "growth", "direction": "higher_better", "lo": -0.2, "hi": 0.3},
    "earnings_growth": {"label": "Earnings Growth (YoY)", "category": "growth", "direction": "higher_better", "lo": -0.3, "hi": 0.5},
    "gross_margin": {"label": "Gross Margin", "category": "profitability", "direction": "higher_better", "lo": 0.0, "hi": 0.6},
    "operating_margin": {"label": "Operating Margin", "category": "profitability", "direction": "higher_better", "lo": -0.1, "hi": 0.35},
    "net_margin": {"label": "Net Margin", "category": "profitability", "direction": "higher_better", "lo": -0.1, "hi": 0.25},
    "roe": {"label": "ROE", "category": "profitability", "direction": "higher_better", "lo": -0.1, "hi": 0.35},
    "roa": {"label": "ROA", "category": "profitability", "direction": "higher_better", "lo": -0.05, "hi": 0.2},
}

PCT_METRICS = {"revenue_growth", "earnings_growth", "gross_margin", "operating_margin", "net_margin", "roe", "roa"}

CATEGORIES = ("valuation", "leverage", "growth", "profitability")


def compute_metrics(fundamentals: dict, market_info: dict) -> dict[str, float | None]:
    def cur(key: str):
        return (fundamentals.get(key) or {}).get("current")

    def prior(key: str):
        return (fundamentals.get(key) or {}).get("prior")

    revenue, revenue_prior = cur("Revenues"), prior("Revenues")
    net_income, net_income_prior = cur("NetIncomeLoss"), prior("NetIncomeLoss")
    assets = cur("Assets")
    liabilities = cur("Liabilities")
    equity = cur("StockholdersEquity")
    assets_current = cur("AssetsCurrent")
    liabilities_current = cur("LiabilitiesCurrent")
    operating_income = cur("OperatingIncomeLoss")
    interest_expense = cur("InterestExpense")
    dep_amort = cur("DepreciationDepletionAndAmortization") or 0
    cost_of_revenue = cur("CostOfRevenue")

    market_cap = market_info.get("marketCap")
    pe = market_info.get("trailingPE")
    pb = market_info.get("priceToBook")

    ebitda = (operating_income + dep_amort) if operating_income is not None else None
    ev_ebitda = None
    if market_cap is not None and liabilities is not None and ebitda:
        # EV approximated as market cap + total liabilities (no separate
        # net-debt/cash breakout tracked) -- a documented v1 simplification.
        ev_ebitda = (market_cap + liabilities) / ebitda if ebitda != 0 else None

    def ratio(numer, denom):
        if numer is None or denom in (None, 0):
            return None
        return numer / denom

    def growth(curr, prev):
        if curr is None or prev in (None, 0):
            return None
        return (curr - prev) / abs(prev)

    return {
        "pe": pe,
        "pb": pb,
        "ev_ebitda": ev_ebitda,
        "debt_to_equity": ratio(liabilities, equity),
        "current_ratio": ratio(assets_current, liabilities_current),
        "interest_coverage": ratio(operating_income, interest_expense),
        "revenue_growth": growth(revenue, revenue_prior),
        "earnings_growth": growth(net_income, net_income_prior),
        "gross_margin": ratio(revenue - cost_of_revenue, revenue) if cost_of_revenue is not None and revenue else None,
        "operating_margin": ratio(operating_income, revenue),
        "net_margin": ratio(net_income, revenue),
        "roe": ratio(net_income, equity),
        "roa": ratio(net_income, assets),
    }


def _normalize(value: float | None, lo: float, hi: float, direction: str) -> float | None:
    if value is None:
        return None
    clamped = max(lo, min(hi, value))
    frac = (clamped - lo) / (hi - lo) if hi > lo else 0.5
    if direction == "lower_better":
        frac = 1 - frac
    return round(frac * 100, 1)


def compute_scores(metrics: dict[str, float | None]) -> tuple[dict, dict]:
    metric_scores = {
        name: _normalize(metrics.get(name), spec["lo"], spec["hi"], spec["direction"])
        for name, spec in METRIC_SPECS.items()
    }

    category_scores: dict[str, float | None] = {}
    for category in CATEGORIES:
        vals = [
            metric_scores[name]
            for name, spec in METRIC_SPECS.items()
            if spec["category"] == category and metric_scores[name] is not None
        ]
        category_scores[category] = round(sum(vals) / len(vals), 1) if vals else None

    return metric_scores, category_scores


def compute_overall(category_scores: dict, weights: dict) -> tuple[float | None, dict]:
    """Weighted average across categories that actually have data,
    renormalizing the configured weights over just those categories."""
    available = {c: w for c, w in weights.items() if category_scores.get(c) is not None}
    total_weight = sum(available.values())
    if total_weight == 0:
        return None, {}

    normalized = {c: w / total_weight for c, w in available.items()}
    overall = sum(category_scores[c] * normalized[c] for c in available)
    return round(overall, 1), normalized


def score_company(ticker: str, as_of_date: date | None = None, weights: dict | None = None) -> dict:
    weights = weights or current_app.config["QR_WEIGHTS"]
    fundamentals = edgar.get_fundamentals(ticker, as_of_date)
    market_info = market_data.get_info(ticker)

    metrics = compute_metrics(fundamentals, market_info)
    metric_scores, category_scores = compute_scores(metrics)
    overall, weights_used = compute_overall(category_scores, weights)

    return {
        "ticker": ticker.upper(),
        "as_of_date": (as_of_date or date.today()).isoformat(),
        "raw_metrics": metrics,
        "metric_scores": metric_scores,
        "category_scores": category_scores,
        "overall_score": overall,
        "weights_used": weights_used,
        "weights_configured": weights,
        "metric_specs": METRIC_SPECS,
    }

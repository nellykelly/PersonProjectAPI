"""The Company Scorer tools Hera may call -- schemas plus a dispatcher.

Unlike the job-tracker tools, these are offered to *every* caller,
signed in or not: `score_company` and `run_backtest` are read/compute
actions over public market data, not writes to shared visible state,
so there is no authorization gate and no confirmation-token flow --
`build_scorer_tools()` always returns both schemas.

The one thing that does need guarding is cost: both underlying calls hit
SEC EDGAR and yfinance over the network, so each tool consumes from the
same rate-limit bucket its equivalent `/projects/qr-quant-scraper` web
route draws from (`app.services.assistant.rate_limit`, action names
"qr_score" / "qr_backtest") before doing any work, so a chat conversation
can't get more requests per hour than the page itself allows.

Like `job_tools.py`, `dispatch_scorer_tool` never raises: a bad ticker, a
network hiccup, a rate-limit hit, or any unexpected exception all come
back as a short string for the model to read and relay.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from flask import current_app

from app.services import backtest, edgar, market_data, quant_score
from app.services.assistant import rate_limit

_MAX_BACKTEST_TICKERS = 10


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_TOOL_SPECS = [
    _tool(
        "score_company",
        "Score a company across four factor categories (valuation, leverage, "
        "growth, profitability) using SEC EDGAR filings and market data, "
        "rolled into a single 0-100 overall score. Only tickers on this "
        "demo's supported whitelist can be scored.",
        {
            "ticker": {
                "type": "string",
                "description": "Stock ticker symbol, e.g. AAPL.",
            },
            "as_of_date": {
                "type": "string",
                "description": "Optional ISO date (YYYY-MM-DD) to score as of -- "
                "uses only filings public by that date. Omit for the latest data.",
            },
        },
        ["ticker"],
    ),
    _tool(
        "run_backtest",
        "Backtest the Company Scorer: score a basket of tickers using only "
        "fundamentals that were public as of N years ago, then compare those "
        "historical scores to actual forward price returns since then, "
        "reporting a per-ticker table and a score-vs-return correlation.",
        {
            "tickers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tickers to include; omit to use the default basket.",
            },
            "years_ago": {
                "type": "integer",
                "description": "How many years back to score from (1-5). Defaults to 1.",
            },
        },
        [],
    ),
]


def build_scorer_tools() -> list[dict]:
    """The tool schemas to hand the model -- always offered, no auth gate.
    Fresh copy each call -- callers must not mutate the module list."""
    return [json.loads(json.dumps(spec)) for spec in _TOOL_SPECS]


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def _fmt_pct(weight: float | None) -> str:
    if weight is None:
        return "n/a"
    return f"{weight * 100:.0f}%"


def _score_company(args: dict) -> str:
    ticker = (args.get("ticker") or "").strip().upper()
    if not ticker:
        return "Which ticker? I need a symbol to score."

    if not market_data.is_valid_ticker(ticker):
        return f"'{ticker}' is not on the supported ticker list for this demo."

    as_of_date = None
    raw_date = (args.get("as_of_date") or "").strip()
    if raw_date:
        try:
            as_of_date = date.fromisoformat(raw_date)
        except ValueError:
            return f"'{raw_date}' isn't a valid date -- use YYYY-MM-DD."

    if not rate_limit.consume("qr_score", current_app.config["QR_SCORE_RATE_LIMIT"]):
        return "hit the hourly limit for scoring, try again later"

    try:
        report = quant_score.score_company(ticker, as_of_date=as_of_date)
    except (market_data.MarketDataError, edgar.EdgarError) as exc:
        return str(exc)

    overall = report.get("overall_score")
    header = f"{report['ticker']} as of {report['as_of_date']}"
    if overall is None:
        return f"{header}: not enough data available to compute a score."

    lines = [f"{header}: overall score {overall}/100"]
    for category in quant_score.CATEGORIES:
        score = report["category_scores"].get(category)
        weight = report["weights_used"].get(category)
        score_str = f"{score}" if score is not None else "n/a"
        lines.append(f"  {category}: {score_str} (weight {_fmt_pct(weight)})")
    return "\n".join(lines)


def _run_backtest(args: dict) -> str:
    tickers = args.get("tickers")
    if tickers is not None:
        if not isinstance(tickers, list) or not all(isinstance(t, str) for t in tickers):
            return "The 'tickers' argument needs to be a list of ticker symbols."
        tickers = [t.strip().upper() for t in tickers if t.strip()][:_MAX_BACKTEST_TICKERS]
        if not tickers:
            tickers = None

    years_ago = args.get("years_ago")
    if years_ago in (None, ""):
        years_ago = 1
    else:
        try:
            years_ago = int(years_ago)
        except (TypeError, ValueError):
            return "The 'years_ago' value needs to be a whole number."
    years_ago = max(1, min(years_ago, 5))

    if not rate_limit.consume("qr_backtest", current_app.config["QR_BACKTEST_RATE_LIMIT"]):
        return "hit the hourly limit for backtests, try again later"

    try:
        result = backtest.run_backtest(tickers=tickers, years_ago=years_ago)
    except Exception as exc:  # noqa: BLE001 - the model must never see a trace
        return f"Backtest failed ({type(exc).__name__})."

    lines = [
        f"Backtest as of {result['as_of_date']} ({years_ago} year(s) back), "
        f"{result['n_valid']}/{result['n_total']} ticker(s) scored:"
    ]
    for row in result["rows"]:
        if "error" in row:
            lines.append(f"  {row['ticker']}: {row['error']}")
        else:
            lines.append(
                f"  {row['ticker']}: score {row['score']}, "
                f"${row['price_then']} -> ${row['price_now']} "
                f"({row['forward_return_pct']:+.2f}%)"
            )
    correlation = result.get("correlation")
    if correlation is None:
        lines.append("Correlation (score vs. forward return): not enough valid data.")
    else:
        lines.append(f"Correlation (score vs. forward return): {correlation}")
    return "\n".join(lines)


_HANDLERS = {
    "score_company": _score_company,
    "run_backtest": _run_backtest,
}


def dispatch_scorer_tool(name: str, arguments: Any) -> str:
    """Execute one tool call and return a short string for the model, always.

    Never raises: an unknown tool, a bad argument type, a rate-limit hit, a
    scoring/backtest error, or any unexpected exception all come back as
    text.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool {name!r}."

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except Exception:  # noqa: BLE001 - ValueError, or RecursionError on pathological nesting
            return f"Could not parse arguments for {name!r}."
    if not isinstance(arguments, dict):
        arguments = {}

    try:
        return handler(arguments)
    except Exception as exc:  # noqa: BLE001 - the model must never see a trace
        return f"That didn't work ({type(exc).__name__})."

"""yfinance wrapper shared by the Trading Simulator and QR Scorer.

Everything here goes through a whitelist check first (never pass raw
user input straight to yfinance), a short-TTL DB cache (cuts down on
repeat calls when many visitors are viewing the same shared trade book),
and a small retry-with-backoff around the actual network call, because
yfinance's free/unauthenticated tier does get rate-limited (observed a
429 from this exact environment during development) -- callers get a
clean MarketDataError instead of a 500, and the UI is expected to show a
"market data temporarily unavailable" state rather than assuming success.

Every outbound call is timed and reported to net_monitor, which is what
powers the Network Sniffer project's "outbound traffic" view.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import yfinance as yf
from flask import current_app

from app.extensions import db
from app.models import PriceCache
from app.services.net_monitor import log_outbound


class MarketDataError(Exception):
    """Raised when a price/history/option-chain lookup can't be satisfied."""


def is_valid_ticker(ticker: str) -> bool:
    whitelist = current_app.config["TICKER_WHITELIST"]
    return ticker.upper() in whitelist


def _require_valid(ticker: str) -> str:
    ticker = ticker.upper()
    if not is_valid_ticker(ticker):
        raise MarketDataError(
            f"'{ticker}' is not on the supported ticker whitelist for this demo."
        )
    return ticker


def _timed_call(source: str, method: str, url: str, fn):
    start = time.time()
    status = 200
    try:
        return fn()
    except Exception:
        status = 599
        raise
    finally:
        log_outbound(source, method, url, status, (time.time() - start) * 1000)


def _with_retry(fn, attempts: int = 3, backoff_seconds: float = 1.5):
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - yfinance raises assorted exception types
            last_exc = exc
            if attempt < attempts - 1:
                time.sleep(backoff_seconds * (attempt + 1))
    raise MarketDataError(f"market data temporarily unavailable: {last_exc}") from last_exc


def get_last_price(ticker: str, use_cache: bool = True) -> float:
    ticker = _require_valid(ticker)
    ttl = current_app.config["PRICE_CACHE_TTL_SECONDS"]

    if use_cache:
        cached = db.session.get(PriceCache, ticker)
        if cached is not None:
            age = (datetime.now(timezone.utc) - cached.fetched_at.replace(tzinfo=timezone.utc)).total_seconds()
            if age < ttl:
                return cached.price

    def _fetch():
        def _do():
            info = yf.Ticker(ticker).fast_info
            price = info.get("lastPrice") or info.get("last_price")
            if price is None:
                raise MarketDataError("no last price in fast_info")
            return float(price)

        return _timed_call("market_data", "GET", f"yfinance://{ticker}/fast_info", _do)

    price = _with_retry(_fetch)

    cached = db.session.get(PriceCache, ticker)
    if cached is not None:
        cached.price = price
        cached.fetched_at = datetime.now(timezone.utc)
    else:
        db.session.add(PriceCache(ticker=ticker, price=price))
    db.session.commit()

    return price


def get_history(ticker: str, period: str = "6mo", interval: str = "1d"):
    ticker = _require_valid(ticker)

    def _do():
        return yf.Ticker(ticker).history(period=period, interval=interval)

    return _with_retry(
        lambda: _timed_call(
            "market_data", "GET", f"yfinance://{ticker}/history?period={period}&interval={interval}", _do
        )
    )


def get_price_near_date(ticker: str, target_date) -> float:
    """Closing price on/just before `target_date` -- used by the QR backtest
    to compute the actual forward return since a historical score date."""
    ticker = _require_valid(ticker)

    def _do():
        start = target_date - timedelta(days=7)
        end = target_date + timedelta(days=1)
        hist = yf.Ticker(ticker).history(start=start, end=end)
        if hist.empty:
            raise MarketDataError(f"no historical price for {ticker} near {target_date}")
        return float(hist["Close"].iloc[-1])

    return _with_retry(
        lambda: _timed_call("market_data", "GET", f"yfinance://{ticker}/history?near={target_date}", _do)
    )


def get_expiries(ticker: str) -> list[str]:
    ticker = _require_valid(ticker)

    def _do():
        return list(yf.Ticker(ticker).options)

    return _with_retry(lambda: _timed_call("market_data", "GET", f"yfinance://{ticker}/options", _do))


def get_option_chain(ticker: str, expiry: str) -> dict:
    """Returns {'calls': [...], 'puts': [...]}, each a list of dicts with
    strike/lastPrice/impliedVolatility/bid/ask/openInterest. yfinance
    exposes the chain itself but no Greeks -- IV here feeds pricing.py's
    local Black-Scholes calc for anything greeks-related."""
    ticker = _require_valid(ticker)

    def _do():
        chain = yf.Ticker(ticker).option_chain(expiry)
        cols = ["strike", "lastPrice", "bid", "ask", "impliedVolatility", "openInterest"]

        def _rows(df):
            return df[cols].to_dict("records") if not df.empty else []

        return {"calls": _rows(chain.calls), "puts": _rows(chain.puts)}

    return _with_retry(
        lambda: _timed_call("market_data", "GET", f"yfinance://{ticker}/option_chain?expiry={expiry}", _do)
    )


def get_info(ticker: str) -> dict:
    """Raw yfinance `.info`-style dict -- market cap, trailing P/E, P/B,
    shares outstanding, etc. Used by the QR scorer's valuation category."""
    ticker = _require_valid(ticker)

    def _do():
        t = yf.Ticker(ticker)
        return dict(t.get_info()) if hasattr(t, "get_info") else dict(t.info)

    return _with_retry(lambda: _timed_call("market_data", "GET", f"yfinance://{ticker}/info", _do))

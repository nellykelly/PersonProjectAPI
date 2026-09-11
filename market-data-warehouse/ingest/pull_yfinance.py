"""Pull every price/market feed yfinance exposes for a set of tickers.

Each `pull_*` function returns a tidy pandas DataFrame with `ticker`,
`source` and `ingested_at` audit columns already attached, ready to land
in the RAW schema unchanged. Fundamentals are deliberately not pulled.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Callable, TypeVar

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

SOURCE = "yfinance"

# Yahoo Finance throttles aggressively once a run touches more than a
# handful of tickers -- this is the single most likely real-world failure
# mode, far more than a malformed response. A short retry with backoff
# absorbs a transient 429/timeout without masking a genuinely bad ticker
# (which still fails after RETRY_ATTEMPTS and is logged, per pull_all's
# per-feed error isolation).
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.5

_T = TypeVar("_T")


def _with_retry(fn: Callable[[], _T], *, what: str) -> _T:
    last_exc: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - yfinance/requests raise many shapes
            last_exc = exc
            if attempt == RETRY_ATTEMPTS:
                break
            wait = RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
            log.warning(
                "%s: attempt %d/%d failed (%s); retrying in %.1fs",
                what, attempt, RETRY_ATTEMPTS, exc, wait,
            )
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc

# The subset of Ticker.info we keep. Everything else in that dict is
# either fundamentals, real-time quote noise, or absent for most symbols.
INFO_FIELDS = {
    "shortName": "short_name",
    "longName": "long_name",
    "quoteType": "quote_type",
    "exchange": "exchange_code",
    "fullExchangeName": "exchange_name",
    "currency": "currency",
    "financialCurrency": "financial_currency",
    "sector": "sector",
    "industry": "industry",
    "country": "country",
    "timeZoneFullName": "timezone_name",
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _stamp(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    df = df.copy()
    df.insert(0, "ticker", ticker)
    df["source"] = SOURCE
    df["ingested_at"] = _now()
    return df


def pull_price_history(ticker: str, *, period: str = "max") -> pd.DataFrame:
    """Daily OHLC (unadjusted) + adjusted close + volume + per-day
    dividend/split flags. `auto_adjust=False` keeps raw OHLC alongside
    Adj Close."""
    t = yf.Ticker(ticker)
    raw = _with_retry(
        lambda: t.history(period=period, interval="1d", auto_adjust=False, actions=True),
        what=f"{ticker}/history",
    )
    if raw.empty:
        return pd.DataFrame()

    raw = raw.reset_index()
    # the datetime column is "Date" for daily bars
    date_col = "Date" if "Date" in raw.columns else raw.columns[0]
    out = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(raw[date_col]).dt.date,
            "open": raw.get("Open"),
            "high": raw.get("High"),
            "low": raw.get("Low"),
            "close": raw.get("Close"),
            "adj_close": raw.get("Adj Close"),
            "volume": raw.get("Volume"),
            "dividend": raw.get("Dividends", 0.0),
            "split_ratio": raw.get("Stock Splits", 0.0),
        }
    )
    return _stamp(out, ticker)


def pull_dividends(ticker: str) -> pd.DataFrame:
    t = yf.Ticker(ticker)
    s = _with_retry(lambda: t.dividends, what=f"{ticker}/dividends")
    if s is None or len(s) == 0:
        return pd.DataFrame()
    out = pd.DataFrame(
        {"ex_date": pd.to_datetime(s.index).date, "amount": s.to_numpy()}
    )
    return _stamp(out, ticker)


def pull_splits(ticker: str) -> pd.DataFrame:
    t = yf.Ticker(ticker)
    s = _with_retry(lambda: t.splits, what=f"{ticker}/splits")
    if s is None or len(s) == 0:
        return pd.DataFrame()
    out = pd.DataFrame(
        {"effective_date": pd.to_datetime(s.index).date, "ratio": s.to_numpy()}
    )
    return _stamp(out, ticker)


def pull_security_info(ticker: str) -> pd.DataFrame:
    """One row of metadata. `info` can be slow or partial; fall back to
    `fast_info` for the fields it covers and leave the rest null."""
    t = yf.Ticker(ticker)
    row: dict[str, object] = {dest: None for dest in INFO_FIELDS.values()}
    try:
        info = _with_retry(lambda: t.get_info(), what=f"{ticker}/get_info") or {}
    except Exception as exc:  # noqa: BLE001 - yfinance raises many shapes
        log.warning("%s: get_info() failed after retries (%s); using fast_info only", ticker, exc)
        info = {}
    for src, dest in INFO_FIELDS.items():
        if info.get(src) not in (None, ""):
            row[dest] = info[src]

    if row["currency"] is None or row["exchange_code"] is None:
        try:
            fi = _with_retry(lambda: t.fast_info, what=f"{ticker}/fast_info")
            row["currency"] = row["currency"] or getattr(fi, "currency", None)
            row["exchange_code"] = row["exchange_code"] or getattr(fi, "exchange", None)
        except Exception:  # noqa: BLE001
            pass

    # Force a real string dtype even when every field came back None (a
    # thin or halted ticker) -- otherwise pandas infers an all-null column
    # as float/object and both DuckDB's CREATE TABLE-from-frame and
    # Snowflake's write_pandas(auto_create_table=True) can land it as a
    # non-text column (H3: broke `trim()` in staging on DuckDB; unverified
    # but plausible on Snowflake, which has no fallback cast for it).
    frame = pd.DataFrame([row]).astype("string")
    return _stamp(frame, ticker)


PULLERS: dict[str, Callable[[str], pd.DataFrame]] = {
    "price_history": pull_price_history,
    "dividends": pull_dividends,
    "splits": pull_splits,
    "security_info": pull_security_info,
}


def pull_all(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Run every puller for every ticker; return one concatenated frame
    per feed. A ticker that fails one feed does not stop the others."""
    frames: dict[str, list[pd.DataFrame]] = {feed: [] for feed in PULLERS}
    for ticker in tickers:
        for feed, fn in PULLERS.items():
            try:
                df = fn(ticker)
            except Exception as exc:  # noqa: BLE001
                log.error("%s/%s: %s", ticker, feed, exc)
                continue
            if not df.empty:
                frames[feed].append(df)
                log.info("%s/%s: %d rows", ticker, feed, len(df))
            else:
                log.info("%s/%s: no data", ticker, feed)
    return {
        feed: (pd.concat(parts, ignore_index=True) if parts else pd.DataFrame())
        for feed, parts in frames.items()
    }

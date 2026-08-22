"""Live watchlist grid for the Trading Simulator.

There's no free real-time market data *stream* to plug into here --
yfinance (like SEC EDGAR) is a pull-only wrapper around a REST endpoint,
not a WebSocket feed, and genuine real-time streaming from an exchange
is normally a paid-provider integration (Polygon.io, IEX, Alpaca, etc.)
requiring its own API key. What this module builds instead: a
server-side poller that refreshes the whitelisted tickers on an
interval and pushes each update to connected browsers instantly over
Server-Sent Events -- the same pattern as the Network Sniffer's live
view -- so the grid *feels* live even though the underlying data is
still yfinance's normal (delayed) quotes.

Two things keep this from burning through yfinance's free-tier rate
limit for no reason:
  1. The poller only runs while at least one browser tab is actually
     subscribed (lazy start/stop, see ensure_poller_running()).
  2. It only fetches during NYSE market hours (a plain weekday +
     9:30-16:00 America/New_York check, no holiday calendar -- a
     documented simplification, not a real trading calendar).
"""
from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from flask import Flask

from app.services import market_data
from app.services.market_data import MarketDataError

_NY_TZ = ZoneInfo("America/New_York")
_MARKET_OPEN = dtime(9, 30)
_MARKET_CLOSE = dtime(16, 0)

_lock = threading.Lock()
_subscribers: set[queue.Queue] = set()
_latest: dict[str, dict] = {}
_poller_thread: threading.Thread | None = None


def is_market_open(now: datetime | None = None) -> bool:
    """Plain NYSE-hours check: Mon-Fri, 9:30-16:00 America/New_York.
    Deliberately does not know about market holidays -- see module
    docstring."""
    now = (now or datetime.now(_NY_TZ)).astimezone(_NY_TZ)
    if now.weekday() >= 5:  # Sat=5, Sun=6
        return False
    return _MARKET_OPEN <= now.time() < _MARKET_CLOSE


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=200)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        _subscribers.discard(q)


def get_snapshot() -> dict[str, dict]:
    with _lock:
        return dict(_latest)


def _broadcast(entry: dict) -> None:
    with _lock:
        subscribers = list(_subscribers)
    for q in subscribers:
        try:
            q.put_nowait(entry)
        except queue.Full:
            pass  # a slow/stalled viewer shouldn't block updates for everyone else


def _poll_once(app: Flask) -> None:
    tickers = app.config["TICKER_WHITELIST"]
    delay = app.config["WATCHLIST_TICKER_DELAY_SECONDS"]

    for ticker in tickers:
        with _lock:
            still_subscribed = bool(_subscribers)
        if not still_subscribed:
            return  # last viewer left mid-cycle -- stop early, don't finish the sweep

        with app.app_context():
            try:
                price = market_data.get_last_price(ticker)
            except MarketDataError:
                time.sleep(delay)
                continue

        with _lock:
            previous = _latest.get(ticker, {}).get("price")
            direction = "up" if previous is not None and price > previous else (
                "down" if previous is not None and price < previous else "flat"
            )
            entry = {
                "ticker": ticker,
                "price": price,
                "direction": direction,
                "updated_at": datetime.now(_NY_TZ).isoformat(),
            }
            _latest[ticker] = entry

        _broadcast(entry)
        time.sleep(delay)


def _poller_loop(app: Flask) -> None:
    global _poller_thread
    try:
        while True:
            with _lock:
                if not _subscribers:
                    return
            if is_market_open():
                _poll_once(app)
                time.sleep(app.config["WATCHLIST_POLL_INTERVAL_SECONDS"])
            else:
                time.sleep(app.config["WATCHLIST_CLOSED_CHECK_INTERVAL_SECONDS"])
    finally:
        with _lock:
            _poller_thread = None


def ensure_poller_running(app: Flask) -> None:
    """Start the background poller if it isn't already running. Safe to
    call on every new subscription -- it's a cheap no-op once a thread
    is alive.

    `app` must be the real Flask instance, not the `current_app` proxy --
    the background thread has no request/app context of its own to
    resolve that proxy through, so callers should pass
    `current_app._get_current_object()`."""
    global _poller_thread
    with _lock:
        if _poller_thread is not None and _poller_thread.is_alive():
            return
        _poller_thread = threading.Thread(
            target=_poller_loop, args=(app,), daemon=True, name="watchlist-poller"
        )
        _poller_thread.start()

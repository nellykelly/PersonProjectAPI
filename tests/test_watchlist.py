import queue
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services import watchlist

NY = ZoneInfo("America/New_York")


# ---------- is_market_open() ----------


def test_market_open_on_a_weekday_during_hours():
    # Wednesday, 2024-01-10, 10:00am ET
    assert watchlist.is_market_open(datetime(2024, 1, 10, 10, 0, tzinfo=NY)) is True


def test_market_closed_before_930am():
    assert watchlist.is_market_open(datetime(2024, 1, 10, 9, 0, tzinfo=NY)) is False


def test_market_closed_at_4pm_and_after():
    assert watchlist.is_market_open(datetime(2024, 1, 10, 16, 0, tzinfo=NY)) is False
    assert watchlist.is_market_open(datetime(2024, 1, 10, 18, 0, tzinfo=NY)) is False


def test_market_closed_on_saturday():
    # 2024-01-13 is a Saturday
    assert watchlist.is_market_open(datetime(2024, 1, 13, 12, 0, tzinfo=NY)) is False


def test_market_closed_on_sunday():
    # 2024-01-14 is a Sunday
    assert watchlist.is_market_open(datetime(2024, 1, 14, 12, 0, tzinfo=NY)) is False


# ---------- pub/sub ----------


def test_subscribe_receives_broadcast_entries():
    q = watchlist.subscribe()
    try:
        watchlist._broadcast({"ticker": "AAPL", "price": 150.0, "direction": "up"})
        entry = q.get(timeout=1)
        assert entry["ticker"] == "AAPL"
    finally:
        watchlist.unsubscribe(q)


def test_unsubscribe_stops_delivery():
    q = watchlist.subscribe()
    watchlist.unsubscribe(q)
    watchlist._broadcast({"ticker": "MSFT", "price": 400.0, "direction": "flat"})
    with pytest.raises(queue.Empty):
        q.get(timeout=0.2)


# ---------- get_snapshot() / _poll_once() direction tracking ----------


def test_poll_once_computes_direction_and_updates_snapshot(app, monkeypatch):
    prices = iter([100.0, 105.0, 105.0, 95.0])
    monkeypatch.setattr(watchlist.market_data, "get_last_price", lambda ticker, use_cache=True: next(prices))
    monkeypatch.setattr(watchlist, "_latest", {})  # isolate from any other test's snapshot state
    app.config["TICKER_WHITELIST"] = ["AAPL"]
    app.config["WATCHLIST_TICKER_DELAY_SECONDS"] = 0

    # _poll_once bails out early with no subscribers (see the module docstring
    # on why the poller is lazy) -- need at least one to actually fetch.
    q = watchlist.subscribe()
    try:
        watchlist._poll_once(app)
        assert watchlist.get_snapshot()["AAPL"]["direction"] == "flat"  # first observation, no prior price

        watchlist._poll_once(app)
        assert watchlist.get_snapshot()["AAPL"]["direction"] == "up"

        watchlist._poll_once(app)
        assert watchlist.get_snapshot()["AAPL"]["direction"] == "flat"

        watchlist._poll_once(app)
        assert watchlist.get_snapshot()["AAPL"]["direction"] == "down"
    finally:
        watchlist.unsubscribe(q)


def test_poll_once_stops_early_once_last_subscriber_leaves(app, monkeypatch):
    calls = []

    def fake_price(ticker, use_cache=True):
        calls.append(ticker)
        if len(calls) == 1:
            watchlist.unsubscribe(q)  # simulate the only viewer disconnecting mid-sweep
        return 100.0

    monkeypatch.setattr(watchlist.market_data, "get_last_price", fake_price)
    app.config["TICKER_WHITELIST"] = ["AAPL", "MSFT", "GOOGL"]
    app.config["WATCHLIST_TICKER_DELAY_SECONDS"] = 0

    q = watchlist.subscribe()
    watchlist._poll_once(app)
    assert calls == ["AAPL"]  # never got to MSFT/GOOGL once the subscriber left


def test_ensure_poller_running_starts_and_is_idempotent(app):
    app.config["TICKER_WHITELIST"] = []  # nothing to actually fetch
    # fast cycling regardless of real market-open state at test time, so the
    # loop notices unsubscription quickly instead of possibly sleeping up to
    # WATCHLIST_CLOSED_CHECK_INTERVAL_SECONDS (default 60s) before rechecking
    app.config["WATCHLIST_POLL_INTERVAL_SECONDS"] = 0.01
    app.config["WATCHLIST_CLOSED_CHECK_INTERVAL_SECONDS"] = 0.01
    q = watchlist.subscribe()
    try:
        watchlist.ensure_poller_running(app)
        thread_first = watchlist._poller_thread
        assert thread_first is not None
        assert thread_first.is_alive()

        watchlist.ensure_poller_running(app)  # should not spawn a second thread
        assert watchlist._poller_thread is thread_first
    finally:
        watchlist.unsubscribe(q)
        # let the poller loop notice no subscribers remain and exit on its own
        for _ in range(50):
            if watchlist._poller_thread is None:
                break
            time.sleep(0.05)

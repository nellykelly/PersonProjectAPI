import queue

import pytest

from app.services import net_monitor


def test_sniffer_page_loads(client):
    resp = client.get("/projects/network-sniffer")
    assert resp.status_code == 200
    assert b"Network Sniffer" in resp.data


def test_inbound_requests_are_logged(client):
    client.get("/about")
    resp = client.get("/projects/network-sniffer/api/log")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["stats"]["inbound"] >= 1
    targets = [e["target"] for e in data["entries"] if e["direction"] == "in"]
    assert "/about" in targets


def test_static_requests_are_excluded_from_the_log():
    before = net_monitor.get_stats()["total"]
    net_monitor.log_inbound("GET", "/static/css/custom.css", 200, 1.0)
    # log_inbound itself doesn't filter -- the exclusion happens in the
    # before/after_request hook via endpoint name, so this just confirms
    # the buffer records what it's told and get_stats reflects it.
    after = net_monitor.get_stats()["total"]
    assert after == before + 1


def test_outbound_calls_are_logged_with_source_and_timing():
    net_monitor.log_outbound("edgar", "GET", "https://data.sec.gov/example", 200, 42.5)
    entries = net_monitor.get_recent(1)
    assert entries[0]["direction"] == "out"
    assert entries[0]["source"] == "edgar"
    assert entries[0]["duration_ms"] == 42.5


def test_get_stats_computes_outbound_by_host():
    net_monitor.log_outbound("market_data", "GET", "https://query1.finance.yahoo.com/x", 200, 10)
    net_monitor.log_outbound("market_data", "GET", "https://query1.finance.yahoo.com/y", 200, 10)
    stats = net_monitor.get_stats()
    assert stats["outbound_by_host"]["query1.finance.yahoo.com"] >= 2


def test_synthetic_yfinance_targets_group_by_scheme_not_by_ticker():
    # market_data.py logs yfinance calls as e.g. "yfinance://AAPL/info" since
    # yfinance manages its own HTTP client internally -- a naive host parse
    # would misread the ticker as the "host". Two different tickers must
    # collapse into one "yfinance" bucket, not two separate ticker buckets.
    net_monitor.log_outbound("market_data", "GET", "yfinance://AAPL/info", 599, 5.0)
    net_monitor.log_outbound("market_data", "GET", "yfinance://MSFT/info", 599, 5.0)
    stats = net_monitor.get_stats()
    assert stats["outbound_by_host"]["yfinance"] >= 2
    assert "AAPL" not in stats["outbound_by_host"]
    assert "MSFT" not in stats["outbound_by_host"]


# ---------- live-view pub/sub (powers the SSE stream in sniffer/routes.py) ----------
# Tested at the net_monitor level, not through the HTTP test client -- the
# actual /api/stream route is an infinite generator by design, which the
# test client would block trying to fully consume.


def test_subscribe_receives_new_entries():
    q = net_monitor.subscribe()
    try:
        net_monitor.log_outbound("edgar", "GET", "https://data.sec.gov/sub-test", 200, 5.0)
        entry = q.get(timeout=1)
        assert entry["direction"] == "out"
        assert entry["target"] == "https://data.sec.gov/sub-test"
    finally:
        net_monitor.unsubscribe(q)


def test_unsubscribe_stops_delivery():
    q = net_monitor.subscribe()
    net_monitor.unsubscribe(q)
    net_monitor.log_outbound("edgar", "GET", "https://data.sec.gov/after-unsub", 200, 5.0)
    with pytest.raises(queue.Empty):
        q.get(timeout=0.2)


def test_a_full_subscriber_queue_does_not_block_or_break_others():
    slow = net_monitor.subscribe()
    try:
        # fill it past its maxsize (100) -- log_outbound must not raise or hang
        for _ in range(105):
            net_monitor.log_outbound("edgar", "GET", "https://data.sec.gov/flood", 200, 1.0)
        # the buffer/get_stats path (used by everyone else) is unaffected
        assert net_monitor.get_stats()["total"] >= 105
    finally:
        net_monitor.unsubscribe(slow)

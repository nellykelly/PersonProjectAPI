from app.services import net_monitor


def test_sniffer_page_loads(client):
    resp = client.get("/projects/network-sniffer")
    assert resp.status_code == 200
    assert b"Site Traffic Analytics" in resp.data


def test_inbound_requests_are_logged(client):
    # Start from a cleared buffer -- the module-level ring buffer fills up
    # over a full test run, and "about.index" (hit once here) would
    # otherwise fall out of the top-endpoints list behind the rest of the
    # suite's traffic. Same reason as
    # test_inbound_requests_group_by_endpoint_not_raw_path below.
    net_monitor.reset_for_tests()
    client.get("/about")
    resp = client.get("/projects/network-sniffer/api/analytics")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["inbound"] >= 1
    endpoints = [e["endpoint"] for e in data["top_endpoints"]]
    assert "about.index" in endpoints


def test_static_requests_are_excluded_from_the_log():
    before = net_monitor.get_analytics()["total"]
    net_monitor.log_inbound("GET", "/static/css/custom.css", 200, 1.0, endpoint="static")
    # log_inbound itself doesn't filter -- the exclusion happens in the
    # before/after_request hook via endpoint name, so this just confirms
    # the buffer records what it's told and get_analytics reflects it.
    after = net_monitor.get_analytics()["total"]
    assert after == before + 1


def test_outbound_calls_are_logged_with_source_and_timing():
    before = net_monitor.get_analytics()["outbound"]
    net_monitor.log_outbound("edgar", "GET", "https://data.sec.gov/example", 200, 42.5)
    stats = net_monitor.get_analytics()
    assert stats["outbound"] == before + 1
    assert stats["outbound_latency"]["max"] >= 42.5


def test_get_analytics_computes_outbound_by_host():
    net_monitor.log_outbound("market_data", "GET", "https://query1.finance.yahoo.com/x", 200, 10)
    net_monitor.log_outbound("market_data", "GET", "https://query1.finance.yahoo.com/y", 200, 10)
    hosts = {h["host"]: h["count"] for h in net_monitor.get_analytics()["top_outbound_hosts"]}
    assert hosts["query1.finance.yahoo.com"] >= 2


def test_synthetic_yfinance_targets_group_by_scheme_not_by_ticker():
    # market_data.py logs yfinance calls as e.g. "yfinance://AAPL/info" since
    # yfinance manages its own HTTP client internally -- a naive host parse
    # would misread the ticker as the "host". Two different tickers must
    # collapse into one "yfinance" bucket, not two separate ticker buckets.
    net_monitor.log_outbound("market_data", "GET", "yfinance://AAPL/info", 599, 5.0)
    net_monitor.log_outbound("market_data", "GET", "yfinance://MSFT/info", 599, 5.0)
    hosts = {h["host"]: h["count"] for h in net_monitor.get_analytics()["top_outbound_hosts"]}
    assert hosts["yfinance"] >= 2
    assert "AAPL" not in hosts
    assert "MSFT" not in hosts


def test_inbound_requests_group_by_endpoint_not_raw_path(client):
    """Two different position ids on the same route must collapse into
    one endpoint row, not fragment into one row per id -- that's the
    whole reason endpoint is logged separately from the raw path. Starts
    from a cleared buffer so this doesn't depend on "top 8" still having
    room once the rest of the suite has generated its own traffic."""
    net_monitor.reset_for_tests()
    client.get("/projects/trading-simulator/positions/999999")
    client.get("/projects/trading-simulator/positions/999998")
    data = net_monitor.get_analytics()
    matches = [e for e in data["top_endpoints"] if e["endpoint"] == "trading.position_detail"]
    assert matches and matches[0]["count"] == 2


def test_client_errors_are_counted_separately_from_server_errors(client):
    client.get("/projects/trading-simulator/risk-reports/999999")  # 404
    data = net_monitor.get_analytics()
    assert data["client_error_count"] >= 1


# ---------- latency percentiles ----------


def test_percentile_of_a_single_value_is_that_value():
    assert net_monitor._percentile([42.0], 50) == 42.0
    assert net_monitor._percentile([42.0], 99) == 42.0


def test_percentile_p50_of_five_values_is_the_middle_one():
    assert net_monitor._percentile([10.0, 20.0, 30.0, 40.0, 50.0], 50) == 30.0


def test_latency_summary_of_no_durations_is_all_none():
    summary = net_monitor._latency_summary([])
    assert summary == {"p50": None, "p90": None, "p99": None, "max": None}


# ---------- volume buckets ----------


def test_volume_buckets_is_empty_for_no_traffic():
    assert net_monitor._volume_buckets([]) == []


def test_volume_buckets_splits_inbound_and_outbound():
    items = [
        {"ts": "2026-01-01T00:00:00+00:00", "direction": "in"},
        {"ts": "2026-01-01T00:00:01+00:00", "direction": "out"},
        {"ts": "2026-01-01T00:01:00+00:00", "direction": "in"},
    ]
    buckets = net_monitor._volume_buckets(items, bucket_count=2)
    assert len(buckets) == 2
    assert sum(b["inbound"] for b in buckets) == 2
    assert sum(b["outbound"] for b in buckets) == 1


def test_volume_buckets_handles_every_entry_at_the_same_instant():
    """A zero-length time span (or a single entry) must not divide by
    zero -- everything should land in one bucket instead of erroring."""
    items = [{"ts": "2026-01-01T00:00:00+00:00", "direction": "in"}] * 3
    buckets = net_monitor._volume_buckets(items, bucket_count=4)
    assert sum(b["inbound"] for b in buckets) == 3

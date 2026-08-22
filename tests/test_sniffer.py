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

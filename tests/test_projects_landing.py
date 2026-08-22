def test_projects_landing_lists_all_three(client):
    resp = client.get("/projects")
    assert resp.status_code == 200
    assert b"Trading Simulator" in resp.data
    assert b"Quant Scorer" in resp.data or b"QR" in resp.data
    assert b"Network Sniffer" in resp.data


def test_projects_landing_links_resolve(client):
    resp = client.get("/projects")
    assert resp.status_code == 200
    for path in (
        "/projects/trading-simulator",
        "/projects/qr-quant-scraper",
        "/projects/network-sniffer",
    ):
        assert path.encode() in resp.data

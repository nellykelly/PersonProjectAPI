def test_projects_landing_lists_all_five(client):
    resp = client.get("/projects")
    assert resp.status_code == 200
    assert b"Trading Simulator" in resp.data
    assert b"Quant Scorer" in resp.data or b"QR" in resp.data
    assert b"Pipeline World" in resp.data
    assert b"SRE Infra Layer" in resp.data
    assert b"Network Sniffer" in resp.data


def test_projects_landing_links_resolve(client):
    resp = client.get("/projects")
    assert resp.status_code == 200
    for path in (
        "/projects/trading-simulator",
        "/projects/qr-quant-scraper",
        "/projects/pipeline-world",
        "/projects/sre-infra",
        "/projects/network-sniffer",
    ):
        assert path.encode() in resp.data


def test_projects_landing_lists_earlier_projects(client):
    resp = client.get("/projects")
    assert b"Beeznest" in resp.data
    assert b"Timed-Squares" in resp.data

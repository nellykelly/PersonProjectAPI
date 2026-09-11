LISTED_PROJECTS = (
    ("Trading Simulator", "/projects/trading-simulator"),
    ("Company Scorer", "/projects/qr-quant-scraper"),
    ("Pipeline World", "/projects/pipeline-world"),
    ("SRE Infra Layer", "/projects/sre-infra"),
    ("Site Traffic Analytics", "/projects/network-sniffer"),
    ("Timed-Squares", "/projects/timed-squares"),
    ("Tiny JVM", "/projects/tiny-jvm"),
    ("Market Data Warehouse", "/projects/market-warehouse"),
    ("Top Interview 150 Tracker", "/leetcode-150"),
    ("AI Assistant", "/assistant"),
)


def test_projects_landing_lists_every_active_project(client):
    resp = client.get("/projects")
    assert resp.status_code == 200
    for title, _ in LISTED_PROJECTS:
        assert title.encode() in resp.data


def test_projects_landing_links_resolve(client):
    resp = client.get("/projects")
    assert resp.status_code == 200
    for _, path in LISTED_PROJECTS:
        assert path.encode() in resp.data


def test_landing_page_links_the_trading_simulator(client):
    """The home page's featured grid is built from the same filtered list
    as /projects, so a listed project shows up on both."""
    resp = client.get("/")
    assert b"/projects/trading-simulator" in resp.data
    assert b"Trading Simulator" in resp.data


def test_project_counts_on_the_landing_page_match_what_is_listed(client):
    """Both counts are rendered from the filtered list rather than typed
    into the template, which is what let them drift before."""
    resp = client.get("/")
    assert str(len(LISTED_PROJECTS)).encode() + b" projects" in resp.data


def test_projects_landing_lists_earlier_projects(client):
    resp = client.get("/projects")
    assert b"Beeznest" in resp.data


def test_earlier_project_links_out(client):
    resp = client.get("/projects")
    assert b"https://github.com/nellykelly/BezzNest" in resp.data

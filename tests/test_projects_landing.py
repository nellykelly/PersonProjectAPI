LISTED_PROJECTS = (
    ("Company Scorer", "/projects/qr-quant-scraper"),
    ("Pipeline World", "/projects/pipeline-world"),
    ("SRE Infra Layer", "/projects/sre-infra"),
    ("Site Traffic Analytics", "/projects/network-sniffer"),
    ("Timed-Squares", "/projects/timed-squares"),
    ("Top Interview 150 Tracker", "/leetcode-150"),
)

# On hold: still runs, still reachable at its own URL, but must not be
# linked from anywhere on the site.
ON_HOLD_PATH = "/projects/trading-simulator"


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


def test_projects_landing_does_not_link_the_on_hold_project(client):
    resp = client.get("/projects")
    assert ON_HOLD_PATH.encode() not in resp.data
    assert b"Trading Simulator" not in resp.data


def test_landing_page_does_not_link_the_on_hold_project(client):
    """The home page's featured grid is built from the same filtered list,
    so a project put on hold drops off both without a second edit."""
    resp = client.get("/")
    assert ON_HOLD_PATH.encode() not in resp.data
    assert b"Trading Simulator" not in resp.data


def test_on_hold_project_still_works_at_its_own_url(client):
    """Hidden from listings is a display decision, not access control --
    the routes are untouched and a direct link still works."""
    resp = client.get(ON_HOLD_PATH)
    assert resp.status_code == 200
    assert b"On hold" in resp.data


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

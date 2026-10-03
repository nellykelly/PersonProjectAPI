"""Server side of the Clarity + PostHog analytics (see README, "Analytics").

The browser half (static/js/analytics.js) is exercised separately in a real
browser; here we pin what the server must guarantee: nothing renders unless
configured, config is validated before reaching an inline <script>, the
current project is derived from PROJECTS, and gated pages never load it.
"""
import pytest

CLARITY_ID = "abcd1234ef"
PH_KEY = "phc_testkey12345"


@pytest.fixture
def configured(app):
    app.config.update(
        CLARITY_PROJECT_ID=CLARITY_ID,
        POSTHOG_API_KEY=PH_KEY,
        POSTHOG_HOST="https://eu.i.posthog.com",
    )
    return app


def test_nothing_rendered_when_unconfigured(client):
    html = client.get("/").get_data(as_text=True)
    assert "__SITE_ANALYTICS__" not in html
    assert "clarity.ms" not in html
    assert "analytics.js" not in html
    csp = client.get("/").headers["Content-Security-Policy-Report-Only"]
    assert "clarity" not in csp and "posthog" not in csp


def test_both_tools_rendered_when_configured(configured, client):
    html = client.get("/").get_data(as_text=True)
    assert "https://www.clarity.ms/tag/" in html
    assert CLARITY_ID in html and PH_KEY in html
    assert "https://eu.i.posthog.com" in html
    assert "https://eu-assets.i.posthog.com" in html
    assert html.count("js/analytics.js") == 1  # loaded once -> initialised once
    csp = client.get("/").headers["Content-Security-Policy-Report-Only"]
    assert "https://www.clarity.ms" in csp and "https://eu-assets.i.posthog.com" in csp


def test_each_tool_is_independent(app, client):
    app.config.update(CLARITY_PROJECT_ID=CLARITY_ID, POSTHOG_API_KEY="")
    html = client.get("/").get_data(as_text=True)
    assert "clarity.ms/tag" in html and '"posthogKey": null' in html
    app.config.update(CLARITY_PROJECT_ID="", POSTHOG_API_KEY=PH_KEY)
    html = client.get("/").get_data(as_text=True)
    assert "clarity.ms/tag" not in html and PH_KEY in html


@pytest.mark.parametrize("bad", ['x"});alert(1);//', "has space", "<script>", "ab"])
def test_malformed_ids_are_dropped_not_interpolated(app, client, bad):
    app.config.update(CLARITY_PROJECT_ID=bad, POSTHOG_API_KEY=bad)
    html = client.get("/").get_data(as_text=True)
    assert "__SITE_ANALYTICS__" not in html


def test_posthog_host_must_be_an_http_origin(monkeypatch):
    from app.config import _analytics_host

    fallback = "https://us.i.posthog.com"
    monkeypatch.setenv("PH_H", "javascript:alert(1)")
    assert _analytics_host("PH_H", fallback) == fallback
    monkeypatch.setenv("PH_H", "https://eu.i.posthog.com/")
    assert _analytics_host("PH_H", fallback) == "https://eu.i.posthog.com"


def test_project_pages_carry_project_metadata(configured, client):
    html = client.get("/projects/market-warehouse").get_data(as_text=True)
    assert '"slug": "market-warehouse"' in html
    assert '"name": "Market Data Warehouse"' in html
    html = client.get("/projects/pipeline-world").get_data(as_text=True)
    assert '"slug": "pipeline-world"' in html
    html = client.get("/assistant").get_data(as_text=True)
    assert '"slug": "assistant"' in html


def test_non_project_pages_have_no_project(configured, client):
    for path in ("/", "/about", "/contact"):
        assert '"project": null' in client.get(path).get_data(as_text=True)


def test_resume_path_is_exposed_for_resume_tracking(configured, client):
    from app import RESUME_PATH

    assert f"/static/{RESUME_PATH}" in client.get("/about").get_data(as_text=True)


def test_assistant_inputs_are_masked_for_clarity(client):
    html = client.get("/assistant").get_data(as_text=True)
    if 'id="asst-input"' in html:  # the offline panel has no chat box
        assert html.count('data-clarity-mask="True"') >= 2


def test_gated_pages_never_load_analytics(configured, client):
    for path in ("/family", "/job-tracker", "/documentation/interview", "/projects/trading-bot"):
        resp = client.get(path)
        assert "__SITE_ANALYTICS__" not in resp.get_data(as_text=True), path

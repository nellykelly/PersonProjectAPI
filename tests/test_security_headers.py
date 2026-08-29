"""Baseline hardening headers set by the app-wide after_request hook."""


def test_core_headers_on_every_response(client):
    resp = client.get("/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in resp.headers["Permissions-Policy"]


def test_headers_present_on_json_endpoints_and_errors(client):
    # a 404 still goes through after_request
    resp = client.get("/definitely-not-a-real-page")
    assert resp.status_code == 404
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_csp_is_report_only_for_now(client):
    resp = client.get("/")
    # enforcing CSP would need nonces/SRI first -- report-only until then
    assert "Content-Security-Policy" not in resp.headers
    csp = resp.headers["Content-Security-Policy-Report-Only"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_hsts_is_not_sent_in_debug_or_testing(client):
    # TestingConfig -> testing is True -> no HSTS (it only makes sense over
    # real HTTPS in production anyway)
    resp = client.get("/")
    assert "Strict-Transport-Security" not in resp.headers

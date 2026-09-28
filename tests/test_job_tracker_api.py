"""The bearer-token JSON API at /api/job-tracker.

Access control first (fails closed, rejects missing/wrong tokens, never
touches a cookie session), then the create/read/update contract a local
Claude Code client relies on: duplicate detection, unknown-field
rejection, append_notes, and the audit trail attributed to source="api".
"""
import pytest

from app.blueprints.job_tracker_api.routes import token_hash
from app.extensions import db
from app.models import JobApplication, JobApplicationEvent

TOKEN = "test-token-abc123"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BASE = "/api/job-tracker/applications"


@pytest.fixture()
def api(app, client):
    app.config["JOB_TRACKER_API_TOKEN_SHA256"] = token_hash(TOKEN)
    yield client
    app.config["JOB_TRACKER_API_TOKEN_SHA256"] = None


def _create(api, **fields):
    body = {"company_name": "Acme", "role_title": "Backend Engineer", **fields}
    return api.post(BASE, json=body, headers=AUTH)


# ---------- access control ----------


def test_unconfigured_api_fails_closed(client):
    resp = client.get(BASE, headers=AUTH)
    assert resp.status_code == 503
    assert "applications" not in resp.get_json()


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN}, {"Authorization": "Bearer "}],
)
def test_missing_or_wrong_token_is_401(api, headers):
    resp = api.get(BASE, headers=headers)
    assert resp.status_code == 401


def test_writes_are_rejected_without_a_token(api, app):
    resp = api.post(BASE, json={"company_name": "Acme", "role_title": "SRE"})
    assert resp.status_code == 401
    with app.app_context():
        assert JobApplication.query.count() == 0


def test_unlocked_board_session_does_not_open_the_api(api, app):
    # A browser that has unlocked /job-tracker carries a session cookie;
    # that must not stand in for the bearer token.
    with api.session_transaction() as sess:
        sess["job_tracker_unlocked"] = True
    assert api.get(BASE).status_code == 401


def test_responses_are_noindex(api):
    resp = api.get(BASE, headers=AUTH)
    assert resp.headers["X-Robots-Tag"] == "noindex, nofollow"


def test_describe_lists_fields_and_statuses(api):
    resp = api.get("/api/job-tracker", headers=AUTH)
    assert resp.status_code == 200
    body = resp.get_json()
    assert "Applied" in body["statuses"]
    assert "tech_stack" in body["fields"]


# ---------- create ----------


def test_create_with_full_detail(api, app):
    resp = _create(
        api,
        job_posting_url="https://example.com/jobs/1",
        date_applied="2026-09-27",
        status="Applied",
        source="LinkedIn",
        cover_letter_used=True,
        company_industry="Fintech",
        company_size_stage="Series B, ~200",
        location_remote_policy="Remote (US)",
        tech_stack=["Python", "Postgres", "AWS"],
        salary_range="$140k-$170k",
        match_grade=82,
        match_notes="Strong on Flask/Postgres, light on Kubernetes.",
        notes="Found via Claude.",
    )
    assert resp.status_code == 201
    app_json = resp.get_json()["application"]
    assert app_json["tech_stack"] == ["Python", "Postgres", "AWS"]
    assert app_json["match_label"] == "Good match"
    assert app_json["cover_letter_used"] is True
    assert app_json["board_url"].endswith(f"/job-tracker/{app_json['id']}")

    with app.app_context():
        event = JobApplicationEvent.query.one()
        assert event.action == "create"
        assert event.source == "api"


def test_create_requires_company_and_role(api):
    resp = api.post(BASE, json={"company_name": "Acme"}, headers=AUTH)
    assert resp.status_code == 400
    assert "required" in resp.get_json()["error"]


def test_create_rejects_unknown_fields(api, app):
    resp = _create(api, salary="100k")
    assert resp.status_code == 400
    body = resp.get_json()
    assert "salary" in body["error"]
    assert "salary_range" in body["allowed"]
    with app.app_context():
        assert JobApplication.query.count() == 0


@pytest.mark.parametrize(
    "fields", [{"status": "Hired"}, {"match_grade": 101}, {"date_applied": "27/09/2026"}]
)
def test_create_rejects_bad_values(api, fields):
    assert _create(api, **fields).status_code == 400


def test_non_object_body_is_400(api):
    resp = api.post(BASE, json=["Acme"], headers=AUTH)
    assert resp.status_code == 400


def test_duplicate_company_and_role_is_409_with_the_existing_card(api):
    first = _create(api).get_json()["application"]
    resp = _create(api, company_name="acme", role_title="backend engineer")
    assert resp.status_code == 409
    assert resp.get_json()["application"]["id"] == first["id"]


def test_duplicate_posting_url_is_409(api):
    _create(api, job_posting_url="https://example.com/jobs/1")
    resp = _create(api, role_title="Platform Engineer", job_posting_url="https://example.com/jobs/1")
    assert resp.status_code == 409


def test_similar_role_at_same_company_is_not_a_duplicate(api):
    _create(api)
    assert _create(api, role_title="Senior Backend Engineer").status_code == 201


def test_allow_duplicate_creates_a_second_card(api, app):
    _create(api)
    assert _create(api, allow_duplicate=True).status_code == 201
    with app.app_context():
        assert JobApplication.query.count() == 2


# ---------- read ----------


def test_list_and_get(api):
    created = _create(api).get_json()["application"]
    _create(api, company_name="Globex", status="Saved")

    listed = api.get(BASE, headers=AUTH).get_json()["applications"]
    assert {a["company_name"] for a in listed} == {"Acme", "Globex"}

    saved = api.get(f"{BASE}?status=Saved", headers=AUTH).get_json()["applications"]
    assert [a["company_name"] for a in saved] == ["Globex"]

    found = api.get(f"{BASE}?q=acm", headers=AUTH).get_json()["applications"]
    assert [a["id"] for a in found] == [created["id"]]

    one = api.get(f"{BASE}/{created['id']}", headers=AUTH)
    assert one.status_code == 200
    assert one.get_json()["application"]["role_title"] == "Backend Engineer"


def test_unknown_status_filter_is_400(api):
    assert api.get(f"{BASE}?status=Hired", headers=AUTH).status_code == 400


def test_get_missing_is_404(api):
    assert api.get(f"{BASE}/999", headers=AUTH).status_code == 404


# ---------- update ----------


def test_patch_updates_only_sent_fields_and_audits_each(api, app):
    app_id = _create(api, salary_range="$100k").get_json()["application"]["id"]
    resp = api.patch(
        f"{BASE}/{app_id}",
        json={"status": "Phone Screen", "tech_stack": "Go, Kafka"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    body = resp.get_json()["application"]
    assert body["status"] == "Phone Screen"
    assert body["tech_stack"] == ["Go", "Kafka"]
    assert body["salary_range"] == "$100k"

    with app.app_context():
        updates = JobApplicationEvent.query.filter_by(action="update").all()
        assert {e.field_name for e in updates} == {"status", "tech_stack"}
        assert {e.source for e in updates} == {"api"}


def test_append_notes_keeps_existing_notes(api):
    app_id = _create(api, notes="Recruiter: Dana").get_json()["application"]["id"]
    resp = api.patch(f"{BASE}/{app_id}", json={"append_notes": "Screen booked"}, headers=AUTH)
    notes = resp.get_json()["application"]["notes"]
    assert notes.startswith("Recruiter: Dana\n\n[")
    assert notes.endswith("] Screen booked")


def test_append_notes_and_notes_together_is_400(api):
    app_id = _create(api).get_json()["application"]["id"]
    resp = api.patch(
        f"{BASE}/{app_id}", json={"notes": "x", "append_notes": "y"}, headers=AUTH
    )
    assert resp.status_code == 400


def test_patch_errors(api):
    app_id = _create(api).get_json()["application"]["id"]
    assert api.patch(f"{BASE}/999", json={"notes": "x"}, headers=AUTH).status_code == 404
    assert api.patch(f"{BASE}/{app_id}", json={}, headers=AUTH).status_code == 400
    assert api.patch(f"{BASE}/{app_id}", json={"bogus": 1}, headers=AUTH).status_code == 400
    assert api.patch(f"{BASE}/{app_id}", json={"match_grade": -1}, headers=AUTH).status_code == 400


def test_there_is_no_delete(api, app):
    app_id = _create(api).get_json()["application"]["id"]
    assert api.delete(f"{BASE}/{app_id}", headers=AUTH).status_code == 405
    with app.app_context():
        assert db.session.get(JobApplication, app_id) is not None


# ---------- red-cell regressions ----------
# The client is an LLM reading untrusted job postings, so a valid token
# does not mean trustworthy input. Each test below is one attack.


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(document.cookie)",
        "JaVaScRiPt:alert(1)",
        " javascript:alert(1)",
        "java\tscript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "//evil.example/x",
        "https:/no-host",
    ],
)
def test_non_http_posting_urls_are_rejected(api, app, url):
    resp = _create(api, job_posting_url=url)
    assert resp.status_code == 400
    assert "http(s)" in resp.get_json()["error"]
    with app.app_context():
        assert JobApplication.query.count() == 0


def test_patch_cannot_smuggle_a_javascript_url_either(api):
    app_id = _create(api).get_json()["application"]["id"]
    resp = api.patch(
        f"{BASE}/{app_id}", json={"job_posting_url": "javascript:alert(1)"}, headers=AUTH
    )
    assert resp.status_code == 400


def test_board_never_renders_a_legacy_javascript_url_as_a_link(app, client):
    # A row written before the service check existed must still not become
    # a clickable javascript: link on the board.
    from werkzeug.security import generate_password_hash

    with app.app_context():
        row = JobApplication(
            company_name="Evil", role_title="Clickme", job_posting_url="javascript:alert(1)"
        )
        db.session.add(row)
        db.session.commit()
    app.config["JOB_TRACKER_PASSWORD_HASH"] = generate_password_hash("pw")
    client.post("/job-tracker/unlock", data={"password": "pw"})
    page = client.get("/job-tracker/board").data
    assert b"Clickme" in page
    assert b"javascript:" not in page


def test_stored_markup_is_escaped_on_the_board(api, app, client):
    _create(api, company_name="<script>alert(1)</script>", notes="<img src=x onerror=alert(1)>")
    from werkzeug.security import generate_password_hash

    app.config["JOB_TRACKER_PASSWORD_HASH"] = generate_password_hash("pw")
    client.post("/job-tracker/unlock", data={"password": "pw"})
    page = client.get("/job-tracker/board").data
    assert b"<script>alert(1)</script>" not in page
    assert b"&lt;script&gt;" in page


@pytest.mark.parametrize(
    "fields",
    [
        {"notes": {"$ne": 1}},
        {"company_name": ["Acme"]},
        {"tech_stack": [{"x": 1}]},
        {"tech_stack": ["t"] * 51},
        {"role_title": "x" * 10_001},
        {"notes": "x" * 50_001},
        {"notes": "a\x00b"},
    ],
)
def test_malformed_or_oversized_values_are_400(api, app, fields):
    assert _create(api, **fields).status_code == 400
    with app.app_context():
        assert JobApplication.query.count() == 0


def test_server_fields_cannot_be_mass_assigned(api):
    for field in ("id", "created_at", "status_updated_at", "updated_at"):
        assert _create(api, **{field: 1}).status_code == 400


def test_oversized_body_is_413(api):
    resp = api.post(
        BASE,
        data='{"company_name":"A","role_title":"B","notes":"' + "x" * 70_000 + '"}',
        headers={**AUTH, "Content-Type": "application/json"},
    )
    assert resp.status_code == 413


def test_form_encoded_post_is_refused(api, app):
    # What a cross-site <form> could send; it isn't JSON, so it's a 400 even
    # with a token (and without one it's a 401 first).
    resp = api.post(BASE, data={"company_name": "A", "role_title": "B"}, headers=AUTH)
    assert resp.status_code == 400


def test_append_notes_cannot_grow_notes_past_the_cap(api):
    app_id = _create(api, notes="x" * 49_990).get_json()["application"]["id"]
    resp = api.patch(f"{BASE}/{app_id}", json={"append_notes": "y" * 100}, headers=AUTH)
    assert resp.status_code == 400


@pytest.mark.parametrize("configured", ["", "not-a-hash", "abc123", "G" * 64])
def test_malformed_configured_hash_fails_closed(app, client, configured):
    app.config["JOB_TRACKER_API_TOKEN_SHA256"] = configured
    # Even a token that hashes to nothing in particular, or an empty one.
    assert client.get(BASE, headers={"Authorization": "Bearer "}).status_code == 503
    assert client.get(BASE, headers=AUTH).status_code == 503


def test_token_hash_is_not_the_token(api, app):
    # The hash itself must not work as a bearer token.
    stored = app.config["JOB_TRACKER_API_TOKEN_SHA256"]
    resp = api.get(BASE, headers={"Authorization": f"Bearer {stored}"})
    assert resp.status_code == 401


def test_auth_is_checked_before_existence(api):
    # No id enumeration without a token.
    assert api.get(f"{BASE}/1").status_code == 401
    assert api.get(f"{BASE}/999999").status_code == 401


def test_no_cors_headers_are_granted(api):
    resp = api.options(BASE, headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in resp.headers
    resp = api.get(BASE, headers={**AUTH, "Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in resp.headers


def test_responses_are_not_cacheable(api):
    assert api.get(BASE, headers=AUTH).headers["Cache-Control"] == "no-store"


def test_failed_auth_attempts_are_rate_limited():
    from app import create_app
    from app.extensions import limiter

    application = create_app("testing")
    application.config["RATELIMIT_ENABLED"] = True
    application.config["JOB_TRACKER_API_RATE_LIMIT"] = "3 per hour"
    application.config["JOB_TRACKER_API_TOKEN_SHA256"] = token_hash(TOKEN)
    limiter.init_app(application)
    client = application.test_client()
    bad = {"Authorization": "Bearer guess"}
    codes = [client.get(BASE, headers=bad).status_code for _ in range(3)]
    assert codes == [401, 401, 401]
    # Bucket shared across endpoints: a different route is throttled too,
    # and so is the right token from the same IP until the window resets.
    assert client.get("/api/job-tracker", headers=bad).status_code == 429
    assert client.get(BASE, headers=AUTH).status_code == 429

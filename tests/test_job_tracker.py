"""The private job-application tracker at /job-tracker.

Same shape of access control as /documentation: nothing reaches an
unauthenticated request, the gate fails closed when unconfigured, and the
section is hidden (no nav link, noindex, robots.txt disallow) on top of
the gate. Below that, the CRUD + audit-log behaviour that the gated
blueprint and (later) the AI assistant both rely on, exercised through
app/services/job_tracker.py.
"""
import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import JobApplication, JobApplicationEvent
from app.services import job_tracker

PASSWORD = "correct-horse-battery-staple"

# Only ever rendered once the section is unlocked.
BOARD_PHRASE = b"Response rate"


@pytest.fixture()
def locked_client(app, client):
    app.config["JOB_TRACKER_PASSWORD_HASH"] = generate_password_hash(PASSWORD)
    yield client
    app.config["JOB_TRACKER_PASSWORD_HASH"] = None


@pytest.fixture()
def unlocked_client(locked_client):
    locked_client.post("/job-tracker/unlock", data={"password": PASSWORD})
    return locked_client


# ---------- the gate ----------


def test_locked_section_asks_for_a_password_and_shows_no_data(locked_client):
    resp = locked_client.get("/job-tracker")
    assert resp.status_code == 200
    assert b"Password" in resp.data
    assert BOARD_PHRASE not in resp.data


def test_board_redirects_to_the_gate_when_locked(locked_client):
    resp = locked_client.get("/job-tracker/board")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/job-tracker")


def test_wrong_password_is_rejected(locked_client):
    resp = locked_client.post("/job-tracker/unlock", data={"password": "nope"})
    assert resp.status_code == 401
    assert b"not right" in resp.data
    assert BOARD_PHRASE not in resp.data


def test_correct_password_unlocks_the_board(locked_client):
    resp = locked_client.post(
        "/job-tracker/unlock", data={"password": PASSWORD}, follow_redirects=True
    )
    assert resp.status_code == 200
    assert BOARD_PHRASE in resp.data


def test_unlock_persists_across_requests(unlocked_client):
    resp = unlocked_client.get("/job-tracker/board")
    assert resp.status_code == 200
    assert BOARD_PHRASE in resp.data


def test_lock_re_gates_the_section(unlocked_client):
    unlocked_client.get("/job-tracker/lock")
    resp = unlocked_client.get("/job-tracker/board")
    assert resp.status_code == 302


def test_gate_fails_closed_when_no_password_is_configured(app, client):
    app.config["JOB_TRACKER_PASSWORD_HASH"] = None

    resp = client.get("/job-tracker")
    assert resp.status_code == 503
    assert BOARD_PHRASE not in resp.data

    resp = client.get("/job-tracker/board")
    assert resp.status_code == 503

    resp = client.post("/job-tracker/unlock", data={"password": PASSWORD})
    assert resp.status_code == 503


def test_no_password_hash_is_committed_to_the_repo():
    from app.config import Config

    assert Config.JOB_TRACKER_PASSWORD_HASH in (None, "")


# ---------- hidden, not just gated ----------


def test_not_linked_from_the_site_nav_or_projects(client):
    """The only place the job tracker is linked from is the *unlocked*
    engineering reference (see test_documentation.py) -- itself password-
    gated and noindex. It must not appear on any public page."""
    for path in ("/about", "/projects", "/", "/contact"):
        resp = client.get(path)
        assert b"/job-tracker" not in resp.data, path


def test_robots_txt_disallows_the_section(client):
    resp = client.get("/robots.txt")
    assert b"Disallow: /job-tracker" in resp.data


def test_every_response_is_noindex(locked_client):
    resp = locked_client.get("/job-tracker")
    assert b"noindex" in resp.data
    assert resp.headers.get("X-Robots-Tag") == "noindex, nofollow"


# ---------- match label rubric ----------


@pytest.mark.parametrize(
    "grade, label",
    [
        (95, "Strong match"),
        (90, "Strong match"),
        (89, "Good match"),
        (75, "Good match"),
        (74, "Worth applying, real gaps"),
        (60, "Worth applying, real gaps"),
        (59, "Reach"),
        (0, "Reach"),
        (None, None),
    ],
)
def test_match_label_boundaries(grade, label):
    assert job_tracker.match_label(grade) == label


# ---------- the service: create / update / delete + audit ----------


def test_create_writes_the_row_and_a_create_event(db):
    app_row = job_tracker.create_application(
        {"company_name": "Ramp", "role_title": "SWE", "status": "Applied"}, source="web"
    )
    assert app_row.id is not None
    assert JobApplication.query.count() == 1

    events = JobApplicationEvent.query.filter_by(application_id=app_row.id).all()
    assert len(events) == 1
    assert events[0].action == "create"
    assert events[0].source == "web"


def test_create_requires_company_and_role(db):
    with pytest.raises(job_tracker.JobTrackerError):
        job_tracker.create_application({"company_name": "Ramp"})
    with pytest.raises(job_tracker.JobTrackerError):
        job_tracker.create_application({"role_title": "SWE"})


def test_create_rejects_an_unknown_status(db):
    with pytest.raises(job_tracker.JobTrackerError):
        job_tracker.create_application(
            {"company_name": "X", "role_title": "Y", "status": "Interviewing"}
        )


def test_create_rejects_an_out_of_range_match_grade(db):
    with pytest.raises(job_tracker.JobTrackerError):
        job_tracker.create_application(
            {"company_name": "X", "role_title": "Y", "match_grade": 140}
        )


def test_update_records_one_event_per_changed_field(db):
    app_row = job_tracker.create_application({"company_name": "Nash", "role_title": "SRE"})
    JobApplicationEvent.query.delete()
    db.session.commit()

    job_tracker.update_application(
        app_row.id,
        {"resume_version": "SRE", "notes": "referred by a friend"},
        source="web",
    )

    events = JobApplicationEvent.query.filter_by(
        application_id=app_row.id, action="update"
    ).all()
    assert {e.field_name for e in events} == {"resume_version", "notes"}


def test_update_with_no_real_change_writes_nothing(db):
    app_row = job_tracker.create_application({"company_name": "Plaid", "role_title": "SWE"})
    JobApplicationEvent.query.delete()
    db.session.commit()

    job_tracker.update_application(app_row.id, {"company_name": "Plaid"}, source="web")
    assert JobApplicationEvent.query.count() == 0


def test_set_status_bumps_status_updated_at_and_logs_it(db):
    app_row = job_tracker.create_application({"company_name": "Rescale", "role_title": "SWE"})
    before = app_row.status_updated_at

    job_tracker.set_status(app_row.id, "Phone Screen", source="assistant")

    refreshed = job_tracker.get_application(app_row.id)
    assert refreshed.status == "Phone Screen"
    assert refreshed.status_updated_at >= before
    ev = JobApplicationEvent.query.filter_by(
        application_id=app_row.id, field_name="status"
    ).one()
    assert ev.old_value == "Applied"
    assert ev.new_value == "Phone Screen"
    assert ev.source == "assistant"


def test_delete_removes_the_row_but_keeps_the_audit_trail(db):
    app_row = job_tracker.create_application({"company_name": "Whatnot", "role_title": "SWE"})
    app_id = app_row.id

    job_tracker.delete_application(app_id, source="web")

    assert db.session.get(JobApplication, app_id) is None
    # The create event plus the delete event both survive, detached.
    remaining = JobApplicationEvent.query.all()
    assert any(e.action == "delete" for e in remaining)
    assert all(e.application_id is None for e in remaining)


def test_summary_stats_math(db):
    for company, status in [
        ("A", "Applied"),
        ("B", "Phone Screen"),
        ("C", "Technical Interview"),
        ("D", "Offer"),
        ("E", "Rejected"),
        ("F", "Ghosted"),
    ]:
        job_tracker.create_application(
            {"company_name": company, "role_title": "R", "status": status}
        )

    stats = job_tracker.summary_stats()
    assert stats["total"] == 6
    assert stats["active_interviews"] == 2  # Phone Screen + Technical Interview
    assert stats["offers"] == 1
    # responded = Phone Screen, Technical Interview, Offer, Rejected = 4 of 6
    assert stats["response_rate"] == 67


def test_find_application_none_one_and_ambiguous(db):
    assert job_tracker.find_application("Nobody") is None

    job_tracker.create_application({"company_name": "Stripe", "role_title": "Backend"})
    assert job_tracker.find_application("stripe").role_title == "Backend"

    job_tracker.create_application({"company_name": "Stripe", "role_title": "Frontend"})
    with pytest.raises(job_tracker.JobTrackerError):
        job_tracker.find_application("Stripe")


# ---------- the routes ----------


def test_add_application_through_the_form(unlocked_client):
    resp = unlocked_client.post(
        "/job-tracker/new",
        data={"company_name": "Notion", "role_title": "Product Engineer", "status": "Applied"},
    )
    assert resp.status_code == 302

    board = unlocked_client.get("/job-tracker/board")
    assert b"Notion" in board.data


def test_inline_status_change_route(unlocked_client, app):
    with app.app_context():
        row = job_tracker.create_application({"company_name": "Figma", "role_title": "SWE"})
        app_id = row.id

    resp = unlocked_client.post(
        f"/job-tracker/{app_id}/status", data={"status": "Onsite/Final"}
    )
    assert resp.status_code == 302

    with app.app_context():
        assert job_tracker.get_application(app_id).status == "Onsite/Final"


def test_edit_route_updates_fields(unlocked_client, app):
    with app.app_context():
        row = job_tracker.create_application({"company_name": "Vercel", "role_title": "SWE"})
        app_id = row.id

    resp = unlocked_client.post(
        f"/job-tracker/{app_id}",
        data={
            "company_name": "Vercel",
            "role_title": "SWE",
            "status": "Applied",
            "resume_version": "Frontend",
            "match_grade": "82",
        },
    )
    assert resp.status_code == 302

    with app.app_context():
        updated = job_tracker.get_application(app_id)
        assert updated.resume_version == "Frontend"
        assert updated.match_grade == 82


def test_delete_route(unlocked_client, app):
    with app.app_context():
        row = job_tracker.create_application({"company_name": "Linear", "role_title": "SWE"})
        app_id = row.id

    resp = unlocked_client.post(f"/job-tracker/{app_id}/delete")
    assert resp.status_code == 302

    with app.app_context():
        assert db.session.get(JobApplication, app_id) is None


def test_edit_page_of_a_missing_application_is_404(unlocked_client):
    assert unlocked_client.get("/job-tracker/999999").status_code == 404


@pytest.mark.parametrize(
    "bad_sort",
    ["events", "tech_stack_list", "bogus", "-tech_stack_list", "'; DROP TABLE"],
)
def test_unknown_sort_param_falls_back_instead_of_crashing(unlocked_client, app, bad_sort):
    """?sort= is user input. getattr() alone would resolve a relationship
    ('events') or a @property ('tech_stack_list') and then blow up in
    order_by(); the whitelist has to catch those."""
    with app.app_context():
        job_tracker.create_application({"company_name": "Datadog", "role_title": "SWE"})
    resp = unlocked_client.get("/job-tracker/board", query_string={"sort": bad_sort})
    assert resp.status_code == 200


def test_status_change_ignores_an_offsite_next(unlocked_client, app):
    with app.app_context():
        row = job_tracker.create_application({"company_name": "Cloudflare", "role_title": "SWE"})
        app_id = row.id

    resp = unlocked_client.post(
        f"/job-tracker/{app_id}/status",
        data={"status": "Phone Screen", "next": "https://evil.example/"},
    )
    assert resp.status_code == 302
    assert "evil.example" not in resp.headers["Location"]
    assert resp.headers["Location"].endswith("/job-tracker/board")


def test_status_change_keeps_a_same_site_filter_url(unlocked_client, app):
    with app.app_context():
        row = job_tracker.create_application({"company_name": "Stripe", "role_title": "SWE"})
        app_id = row.id

    resp = unlocked_client.post(
        f"/job-tracker/{app_id}/status",
        data={"status": "Offer", "next": "/job-tracker/board?status=Offer"},
    )
    assert resp.headers["Location"].endswith("/job-tracker/board?status=Offer")


def test_protocol_relative_next_is_rejected(unlocked_client, app):
    with app.app_context():
        row = job_tracker.create_application({"company_name": "Vanta", "role_title": "SWE"})
        app_id = row.id

    resp = unlocked_client.post(
        f"/job-tracker/{app_id}/status",
        data={"status": "Rejected", "next": "//evil.example/job-tracker"},
    )
    assert resp.headers["Location"].endswith("/job-tracker/board")


def test_forms_carry_a_csrf_token(unlocked_client):
    """CSRF is disabled in the test config, but the token has to be in the
    markup or it would break in every other environment (this blueprint
    is deliberately not csrf-exempt)."""
    resp = unlocked_client.get("/job-tracker/new")
    assert b"csrf_token" in resp.data

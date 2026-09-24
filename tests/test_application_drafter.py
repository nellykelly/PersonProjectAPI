"""app.services.application_drafter -- the drafter -> reviewer -> revise
pass behind /job-tracker/<id>/draft, plus its routes. The LLM is the
ScriptedBackend (same as test_job_discovery.py), and the queue runs
synchronously under TESTING, so start_draft finishes the whole draft
before returning."""
import json

import pytest
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.models import ApplicationDraft, JobApplication, JobDiscoveryRun, JobListing
from app.services import application_drafter as drafter
from app.services import job_discovery
from app.services.assistant.backends import SCRIPTED_BACKEND

_PASSWORD = "correct-horse-battery-staple"
_POSTING = "We are hiring a backend Python engineer to build LLM agents. " * 10


def _draft_json(**overrides):
    body = {
        "fit_verdict": "Good fit: strong Python and agent work.",
        "strengths": ["Python backend", "Production LLM agents"],
        "gaps": ["No Kubernetes"],
        "headline": "Software Engineer, agentic AI",
        "resume_summary": "Backend engineer who ships LLM agents.",
        "resume_bullets": ["Built a LangGraph agent", "Ran a Flask API"],
        "cover_letter": "Dear team,\n\nI build agents.\n\nThanks,\nNelson",
        "why_company": "Your agent platform matches my work.",
        "short_pitch": "I ship production LLM agents in Python.",
    }
    body.update(overrides)
    return {"text": json.dumps(body), "prompt_tokens": 100, "completion_tokens": 50}


@pytest.fixture(autouse=True)
def _setup(app, monkeypatch):
    SCRIPTED_BACKEND.reset()
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    monkeypatch.setattr(job_discovery, "build_candidate_profile", lambda: "RESUME: Python, LangGraph, Flask")
    yield
    SCRIPTED_BACKEND.reset()


@pytest.fixture()
def unlocked_client(app, client):
    app.config["JOB_TRACKER_PASSWORD_HASH"] = generate_password_hash(_PASSWORD)
    client.post("/job-tracker/unlock", data={"password": _PASSWORD})
    yield client
    app.config["JOB_TRACKER_PASSWORD_HASH"] = None


def _make_application(app):
    with app.app_context():
        application = JobApplication(company_name="Acme", role_title="AI Engineer", status="Saved")
        db.session.add(application)
        db.session.commit()
        return application.id


def test_draft_review_revise_stores_the_revised_draft(app, db):
    app_id = _make_application(app)
    SCRIPTED_BACKEND.push(
        _draft_json(),
        {"text": json.dumps({"issues": ["'Ran a Flask API' is vague; name what it served."]})},
        _draft_json(resume_bullets=["Built a LangGraph agent", "Ran the Flask API behind this site"]),
    )
    with app.app_context():
        draft_id = drafter.start_draft(app_id, _POSTING)
        draft = db.session.get(ApplicationDraft, draft_id)
        assert draft.status == "completed"
        assert draft.resume_bullets.splitlines()[1] == "Ran the Flask API behind this site"
        assert "vague" in draft.review_notes
        assert draft.gaps == "No Kubernetes"
        assert draft.groq_prompt_tokens > 0
        # The application itself is never touched -- drafting isn't applying.
        assert db.session.get(JobApplication, app_id).status == "Saved"

    # Three calls: draft, review (separate fresh prompt), revise.
    assert len(SCRIPTED_BACKEND.calls) == 3
    review_system = SCRIPTED_BACKEND.calls[1]["messages"][0]["content"]
    assert "You did not write them" in review_system
    draft_user = SCRIPTED_BACKEND.calls[0]["messages"][1]["content"]
    assert "<<<POSTING" in draft_user and "POSTING>>>" in draft_user


def test_a_clean_review_skips_the_revision_call(app, db):
    app_id = _make_application(app)
    SCRIPTED_BACKEND.push(_draft_json(), {"text": json.dumps({"issues": []})})
    with app.app_context():
        draft = db.session.get(ApplicationDraft, drafter.start_draft(app_id, _POSTING))
        assert draft.status == "completed"
        assert draft.review_notes is None
    assert len(SCRIPTED_BACKEND.calls) == 2


def test_a_broken_revision_keeps_the_first_draft(app, db):
    app_id = _make_application(app)
    SCRIPTED_BACKEND.push(
        _draft_json(), {"text": json.dumps({"issues": ["fix x"]})}, {"text": "not json"}
    )
    with app.app_context():
        draft = db.session.get(ApplicationDraft, drafter.start_draft(app_id, _POSTING))
        assert draft.status == "completed"
        assert draft.cover_letter.startswith("Dear team")
        assert "revision pass failed" in draft.review_notes


def test_dashes_and_curly_quotes_are_stripped_from_the_output(app, db):
    app_id = _make_application(app)
    SCRIPTED_BACKEND.push(
        _draft_json(
            fit_verdict="Moderate fit – strong Python",
            resume_bullets=["Built end‑to‑end “prod vs. prod” checks"],
        ),
        {"text": json.dumps({"issues": []})},
    )
    with app.app_context():
        draft = db.session.get(ApplicationDraft, drafter.start_draft(app_id, _POSTING))
        assert draft.fit_verdict == "Moderate fit, strong Python"
        assert draft.resume_bullets == 'Built end-to-end "prod vs. prod" checks'


def test_an_unparseable_first_draft_fails_the_row(app, db):
    app_id = _make_application(app)
    SCRIPTED_BACKEND.push({"text": "sorry, no"})
    with app.app_context():
        draft = db.session.get(ApplicationDraft, drafter.start_draft(app_id, _POSTING))
        assert draft.status == "failed"
        assert "JSON" in draft.error_message


def test_start_draft_requires_posting_text(app, db):
    app_id = _make_application(app)
    with app.app_context(), pytest.raises(drafter.DraftError, match="Paste the job posting"):
        drafter.start_draft(app_id, "too short")


def test_start_draft_falls_back_to_the_promoted_listings_description(app, db):
    app_id = _make_application(app)
    with app.app_context():
        db.session.add(JobListing(
            company_name="Acme", role_title="AI Engineer", job_posting_url="https://x/1",
            description=_POSTING, source="freehire", status="promoted", promoted_application_id=app_id,
        ))
        db.session.commit()
    SCRIPTED_BACKEND.push(_draft_json(), {"text": json.dumps({"issues": []})})
    with app.app_context():
        draft = db.session.get(ApplicationDraft, drafter.start_draft(app_id, ""))
        assert draft.posting_text == _POSTING.strip()


def test_start_draft_refuses_once_the_groq_budget_is_spent(app, db):
    app_id = _make_application(app)
    with app.app_context():
        limit = app.config.get("GROQ_DAILY_TOKEN_LIMIT", 200000)
        db.session.add(JobDiscoveryRun(status="completed", groq_prompt_tokens=limit))
        db.session.commit()
        with pytest.raises(drafter.DraftError, match="budget"):
            drafter.start_draft(app_id, _POSTING)


def test_draft_tokens_count_against_the_shared_daily_budget(app, db):
    app_id = _make_application(app)
    SCRIPTED_BACKEND.push(_draft_json(), {"text": json.dumps({"issues": []})})
    with app.app_context():
        before = job_discovery.quota_status()["groq_used_today"]
        drafter.start_draft(app_id, _POSTING)
        assert job_discovery.quota_status()["groq_used_today"] > before


def test_deleting_the_application_deletes_its_drafts(app, db):
    from app.services import job_tracker

    app_id = _make_application(app)
    SCRIPTED_BACKEND.push(_draft_json(), {"text": json.dumps({"issues": []})})
    with app.app_context():
        drafter.start_draft(app_id, _POSTING)
        job_tracker.delete_application(app_id)
        assert ApplicationDraft.query.count() == 0


def test_routes_draft_and_render_the_result(app, db, unlocked_client):
    app_id = _make_application(app)
    SCRIPTED_BACKEND.push(_draft_json(), {"text": json.dumps({"issues": ["one"]})}, _draft_json())

    page = unlocked_client.get(f"/job-tracker/{app_id}")
    assert b"Draft application" in page.data

    resp = unlocked_client.post(f"/job-tracker/{app_id}/draft", data={"posting_text": _POSTING})
    assert resp.status_code == 302 and "/drafts/" in resp.headers["Location"]

    body = unlocked_client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Cover letter" in body and "Dear team" in body
    assert "Reviewer flagged 1 issue" in body


def test_route_flashes_an_error_for_missing_posting(app, db, unlocked_client):
    app_id = _make_application(app)
    resp = unlocked_client.post(f"/job-tracker/{app_id}/draft", data={"posting_text": ""})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("#drafts")


def test_draft_page_404s_for_another_applications_draft(app, db, unlocked_client):
    app_id = _make_application(app)
    other_id = _make_application(app)
    SCRIPTED_BACKEND.push(_draft_json(), {"text": json.dumps({"issues": []})})
    with app.app_context():
        draft_id = drafter.start_draft(app_id, _POSTING)
    assert unlocked_client.get(f"/job-tracker/{other_id}/drafts/{draft_id}").status_code == 404


def test_draft_routes_are_behind_the_password_gate(app, db, client):
    app_id = _make_application(app)
    app.config["JOB_TRACKER_PASSWORD_HASH"] = generate_password_hash(_PASSWORD)
    try:
        resp = client.post(f"/job-tracker/{app_id}/draft", data={"posting_text": _POSTING})
        assert "/drafts/" not in resp.headers.get("Location", "")
    finally:
        app.config["JOB_TRACKER_PASSWORD_HASH"] = None
    with app.app_context():
        assert ApplicationDraft.query.count() == 0

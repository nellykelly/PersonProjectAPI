"""Cron grading of tracker cards: job_discovery.grade_pending_applications,
run by the hourly `flask job-tracker rescore`, plus the job_tracker
bookkeeping that keeps grades correct -- a manual grade is never
overwritten, and an LLM grade is cleared and redone when the posting
summary it was based on changes.

Scoring is deterministic via ScriptedBackend, same as test_job_discovery.
"""
import json

import pytest

from app.extensions import db
from app.models import JobApplication, JobApplicationEvent, JobDiscoveryRun, JobListing
from app.services import job_discovery, job_tracker
from app.services.assistant.backends import SCRIPTED_BACKEND

SUMMARY = "Backend engineer, Python/Flask + Postgres, 3+ years, remote US."


@pytest.fixture(autouse=True)
def _scripted(app):
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    SCRIPTED_BACKEND.reset()
    yield
    SCRIPTED_BACKEND.reset()


def _grade_reply(grade=78, notes="Good Python fit."):
    SCRIPTED_BACKEND.push({"text": json.dumps({"grade": grade, "notes": notes})})


def _card(app, **fields):
    with app.app_context():
        data = {"company_name": "Acme", "role_title": "Backend Engineer", **fields}
        return job_tracker.create_application(data, source="api").id


def _get(app, app_id):
    with app.app_context():
        return db.session.get(JobApplication, app_id)


# ---------- the grader ----------


def test_ungraded_card_with_summary_gets_an_llm_grade(app, db):
    app_id = _card(app, posting_summary=SUMMARY)
    _grade_reply(78, "Good Python fit.")

    run = job_discovery.grade_pending_applications()
    assert run.status == "completed" and run.scored == 1

    row = db.session.get(JobApplication, app_id)
    assert row.match_grade == 78
    assert row.match_notes == "Good Python fit."
    assert row.graded_by == "llm"
    events = JobApplicationEvent.query.filter_by(application_id=app_id, action="update").all()
    assert {e.field_name for e in events} == {"match_grade", "match_notes"}
    assert {e.source for e in events} == {"cli"}


def test_grader_sends_the_summary_and_card_details_to_the_model(app, db):
    _card(app, posting_summary=SUMMARY, location_remote_policy="Remote (US)")
    _grade_reply()
    job_discovery.grade_pending_applications()
    prompt = SCRIPTED_BACKEND.calls[-1]["messages"][-1]["content"]
    assert SUMMARY in prompt
    assert "Backend Engineer" in prompt and "Acme" in prompt and "Remote (US)" in prompt


def test_cards_without_a_summary_are_not_graded(app, db):
    _card(app)
    assert job_discovery.grade_pending_applications() is None
    assert JobDiscoveryRun.query.count() == 0


def test_manual_grade_is_never_overwritten(app, db):
    app_id = _card(app, posting_summary=SUMMARY, match_grade=91)
    assert _get(app, app_id).graded_by == "manual"
    assert job_discovery.grade_pending_applications() is None
    assert db.session.get(JobApplication, app_id).match_grade == 91


def test_failed_grade_counts_an_attempt_and_stops_at_the_cap(app, db):
    app.config["JOB_DISCOVERY_MAX_SCORE_ATTEMPTS"] = 2
    app_id = _card(app, posting_summary=SUMMARY)
    SCRIPTED_BACKEND.push({"text": "not json"}, {"text": "still not json"})

    job_discovery.grade_pending_applications()
    job_discovery.grade_pending_applications()
    row = db.session.get(JobApplication, app_id)
    assert row.match_grade is None
    assert row.grade_attempts == 2
    assert job_discovery.grade_pending_applications() is None  # capped


def test_grader_skips_when_the_groq_budget_is_spent(app, db):
    app.config["GROQ_DAILY_TOKEN_LIMIT"] = 100
    _card(app, posting_summary=SUMMARY)
    db.session.add(JobDiscoveryRun(status="completed", groq_prompt_tokens=100))
    db.session.commit()
    assert job_discovery.grade_pending_applications() is None


def test_limit_caps_one_cron_tick(app, db):
    for i in range(3):
        _card(app, role_title=f"Role {i}", posting_summary=SUMMARY)
    _grade_reply()
    _grade_reply()
    run = job_discovery.grade_pending_applications(limit=2)
    assert run.progress_total == 2


# ---------- bookkeeping that keeps grades correct ----------


def test_changing_the_summary_regrades_an_llm_grade(app, db):
    app_id = _card(app, posting_summary=SUMMARY)
    _grade_reply(60, "Old read.")
    job_discovery.grade_pending_applications()

    job_tracker.update_application(app_id, {"posting_summary": SUMMARY + " Go required."}, source="api")
    row = db.session.get(JobApplication, app_id)
    assert row.match_grade is None and row.match_notes is None and row.graded_by is None

    _grade_reply(45, "Go is a gap.")
    job_discovery.grade_pending_applications()
    assert db.session.get(JobApplication, app_id).match_grade == 45


def test_changing_the_summary_keeps_a_manual_grade(app, db):
    app_id = _card(app, posting_summary=SUMMARY, match_grade=88)
    job_tracker.update_application(app_id, {"posting_summary": "Something else."}, source="api")
    assert db.session.get(JobApplication, app_id).match_grade == 88


def test_clearing_the_grade_hands_it_back_to_the_grader(app, db):
    app_id = _card(app, posting_summary=SUMMARY, match_grade=88)
    job_tracker.update_application(app_id, {"match_grade": None}, source="api")
    row = db.session.get(JobApplication, app_id)
    assert row.graded_by is None
    _grade_reply(70)
    job_discovery.grade_pending_applications()
    assert db.session.get(JobApplication, app_id).graded_by == "llm"


def test_resaving_the_web_form_does_not_flip_an_llm_grade_to_manual(app, db):
    app_id = _card(app, posting_summary=SUMMARY)
    _grade_reply(78)
    job_discovery.grade_pending_applications()
    # The edit form posts match_grade back unchanged on every save.
    job_tracker.update_application(app_id, {"match_grade": "78", "notes": "x"}, source="web")
    assert db.session.get(JobApplication, app_id).graded_by == "llm"


def test_summary_is_capped_at_what_the_grader_reads(app, db):
    with pytest.raises(job_tracker.JobTrackerError, match="summarise"):
        job_tracker.create_application(
            {"company_name": "A", "role_title": "B", "posting_summary": "x" * 3001}
        )


# ---------- promoting a Discover listing ----------


def _listing(graded_by, grade):
    listing = JobListing(
        company_name="Acme", role_title="Data Engineer", location="Remote",
        job_posting_url="https://example.com/j/1", description="Build pipelines. " * 400,
        source="Adzuna", status="new", match_grade=grade, graded_by=graded_by,
        match_notes="listing notes",
    )
    db.session.add(listing)
    db.session.commit()
    return listing.id


def test_promoting_keeps_an_llm_grade_as_llm(app, db):
    application = job_discovery.promote_listing(_listing("llm", 81))
    assert application.match_grade == 81 and application.graded_by == "llm"
    assert len(application.posting_summary) == job_tracker.POSTING_SUMMARY_MAX_CHARS


def test_promoting_drops_a_keyword_prescore_so_the_cron_grades_it(app, db):
    application = job_discovery.promote_listing(_listing("keyword", 40))
    assert application.match_grade is None and application.graded_by is None
    assert job_discovery.pending_application_grades() == [application]


# ---------- CLI ----------


def test_rescore_cli_grades_applications(app, db):
    app_id = _card(app, posting_summary=SUMMARY)
    _grade_reply(66)
    result = app.test_cli_runner().invoke(args=["job-tracker", "rescore"])
    assert "Graded 1/1 application(s)" in result.output
    assert db.session.get(JobApplication, app_id).match_grade == 66


def test_rescore_cli_dry_run_lists_pending_applications(app, db):
    _card(app, posting_summary=SUMMARY)
    result = app.test_cli_runner().invoke(args=["job-tracker", "rescore", "--dry-run"])
    assert "Would grade 1 application(s)" in result.output
    assert "Acme - Backend Engineer" in result.output

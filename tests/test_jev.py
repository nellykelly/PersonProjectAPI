"""app.services.jev (Jev first-pass job evaluation) and how
job_discovery.execute_run uses it. No live network: requests.post is
monkeypatched with the answer shapes from langchain-typesafe's own
response types (ChoiceAnswer / ScoreAnswer / NoulAnswer)."""
import json

import pytest

from app.extensions import db
from app.models import JobDiscoveryRun, JobListing
from app.services import jev
from app.services import job_discovery as jd
from app.services.assistant.backends import SCRIPTED_BACKEND


def _choice(top, probs):
    return {"type": "choice", "choice": top, "probabilities": probs, "confidence": 0.8}


def _score(value):
    return {"type": "score", "score": value, "legend": {}, "probabilities": {}, "confidence": 0.8}


def _answers(role="ai_engineer", level="mid", skills=2.7, experience=2.4, fintech=0.1, clearance=0.0):
    role_probs = role if isinstance(role, dict) else {role: 1.0}
    level_probs = level if isinstance(level, dict) else {level: 1.0}
    return {
        "role_family": _choice(max(role_probs, key=role_probs.get), role_probs),
        "seniority": _choice(max(level_probs, key=level_probs.get), level_probs),
        "skills": _score(skills),
        "experience": _score(experience),
        "fintech": {"type": "noul", "noul": fintech},
        "clearance": {"type": "noul", "noul": clearance},
    }


# --------------------------------------------------------------------------
# grading
# --------------------------------------------------------------------------


def test_a_strong_mid_level_ai_role_grades_high():
    ev = jev.grade_from_answers(_answers())
    assert ev.grade >= 80
    assert ev.notes.startswith("Jev: ai engineer (100%), mid level")


def test_calibration_caps_match_the_scoring_guidance():
    # Perfect skills/experience can't lift a people-manager or staff-level
    # posting past the candidate's own observed calibration numbers.
    assert jev.grade_from_answers(_answers(role="eng_manager", skills=3, experience=3)).grade <= 32
    assert jev.grade_from_answers(_answers(role="product_manager", skills=3, experience=3)).grade <= 45
    assert jev.grade_from_answers(_answers(level="staff_plus", skills=3, experience=3)).grade <= 37
    assert jev.grade_from_answers(_answers(level="entry", skills=3, experience=3)).grade <= 40


def test_caps_apply_in_proportion_to_their_probability():
    certain = jev.grade_from_answers(_answers(role="eng_manager")).grade
    maybe = jev.grade_from_answers(_answers(role={"ai_engineer": 0.7, "eng_manager": 0.3})).grade
    clean = jev.grade_from_answers(_answers()).grade
    assert certain < maybe < clean


def test_fintech_bonus_and_clearance_flag():
    base = jev.grade_from_answers(_answers(skills=2, experience=2))
    fin = jev.grade_from_answers(_answers(skills=2, experience=2, fintech=0.9, clearance=0.8))
    assert fin.grade == base.grade + jev._FINTECH_BONUS
    assert "fintech" in fin.notes and "security clearance" in fin.notes


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._body


@pytest.fixture()
def jev_on(app, monkeypatch):
    app.config["TYPESAFE_API_KEY"] = "test-key"
    monkeypatch.setattr(jev, "log_outbound", lambda *a, **k: None)
    yield
    app.config["TYPESAFE_API_KEY"] = ""


def test_evaluate_listing_sends_the_documented_wire_shape(app, jev_on, monkeypatch):
    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, payload=json, headers=headers)
        return _Resp(body={"model": "jev-1.13", "answers": _answers()})

    monkeypatch.setattr(jev.requests, "post", fake_post)
    with app.app_context():
        ev = jev.evaluate_listing("RESUME", {
            "role_title": "AI Engineer", "company_name": "Acme", "description": "Build agents.",
        })
    assert ev.role_family == "ai_engineer"
    assert sent["url"] == "https://api.typesafe.ai/v1/systemone"
    assert sent["headers"]["Authorization"] == "Bearer test-key"
    payload = sent["payload"]
    assert payload["model"] == "jev-latest"
    assert payload["state"]["candidate"] == "RESUME"
    assert payload["state"]["job_posting"]["title"] == "AI Engineer"
    assert {q["type"] for q in payload["questions"].values()} == {"choice", "score", "noul"}
    assert payload["questions"]["skills"]["criteria"] == jev._SKILLS_RUBRIC


def test_http_errors_and_incomplete_answers_raise_unavailable(app, jev_on, monkeypatch):
    listing = {"role_title": "x", "company_name": "y"}
    monkeypatch.setattr(jev.requests, "post", lambda *a, **k: _Resp(status=429, body={}))
    with app.app_context(), pytest.raises(jev.JevUnavailable):
        jev.evaluate_listing("p", listing)
    partial = _answers()
    del partial["skills"]
    monkeypatch.setattr(jev.requests, "post", lambda *a, **k: _Resp(body={"answers": partial}))
    with app.app_context(), pytest.raises(jev.JevUnavailable):
        jev.evaluate_listing("p", listing)


def test_no_key_means_not_configured(app):
    with app.app_context():
        assert not jev.is_configured()
        with pytest.raises(jev.JevUnavailable):
            jev.classify("state", {})


# --------------------------------------------------------------------------
# execute_run with Jev
# --------------------------------------------------------------------------


def _posting(n, title="AI Engineer"):
    return {
        "company_name": f"Co{n}", "role_title": title, "location": "Remote",
        "job_posting_url": f"https://x/{n}", "date_posted": None, "salary_range": None,
        "description": "Build agents in Python.", "source": "freehire", "_keyword": "AI Engineer",
    }


@pytest.fixture()
def stubbed_run(app, db, monkeypatch):
    SCRIPTED_BACKEND.reset()
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    monkeypatch.setattr(jd, "build_candidate_profile", lambda: "RESUME")
    for source in ("remoteok", "arbeitnow", "remotive", "greenhouse", "lever", "ashby", "workable"):
        monkeypatch.setattr(getattr(jd, source), "search", lambda **kwargs: [])
    with app.app_context():
        profile = jd.get_search_profile()
        profile.keywords = "AI Engineer"
        db.session.commit()
        run = JobDiscoveryRun(status="running", phase="Queued", progress_total=1)
        db.session.add(run)
        db.session.commit()
        run_id = run.id
    yield run_id
    SCRIPTED_BACKEND.reset()


def test_jev_triages_everything_and_groq_only_scores_the_best(app, jev_on, stubbed_run, monkeypatch):
    app.config.update(JEV_LLM_THRESHOLD=55, JOB_DISCOVERY_MAX_LLM_PER_RUN=1)
    postings = [_posting(1), _posting(2, "Engineering Manager"), _posting(3)]
    monkeypatch.setattr(jd.adzuna, "search", lambda **kwargs: ([], 0, False))
    monkeypatch.setattr(jd.freehire, "search", lambda **kwargs: postings)
    by_title = {
        "Co1": _answers(skills=3, experience=3),  # best -> the one Groq call
        "Co2": _answers(role="eng_manager"),  # capped low, never reaches Groq
        "Co3": _answers(skills=2, experience=2),  # above threshold, but over the Groq cap
    }
    monkeypatch.setattr(
        jev.requests, "post",
        lambda url, json=None, **k: _Resp(body={"answers": by_title[json["state"]["job_posting"]["company"]]}),
    )
    SCRIPTED_BACKEND.push({"text": json.dumps({"grade": 88, "notes": "Great agent fit."})})

    with app.app_context():
        jd.execute_run(stubbed_run)
        rows = {r.company_name: r for r in JobListing.query.all()}
        assert rows["Co1"].graded_by == "llm" and rows["Co1"].match_grade == 88
        assert "Great agent fit." in rows["Co1"].match_notes and "Jev:" in rows["Co1"].match_notes
        assert rows["Co2"].graded_by == "jev" and rows["Co2"].match_grade <= 32
        assert rows["Co3"].graded_by == "jev" and "Not sent to the LLM" in rows["Co3"].match_notes
        assert db.session.get(JobDiscoveryRun, stubbed_run).status == "completed"
    assert len(SCRIPTED_BACKEND.calls) == 1


def test_a_failed_jev_call_falls_back_to_the_keyword_prescore(app, jev_on, stubbed_run, monkeypatch):
    monkeypatch.setattr(jd.adzuna, "search", lambda **kwargs: ([], 0, False))
    monkeypatch.setattr(jd.freehire, "search", lambda **kwargs: [_posting(1)])
    monkeypatch.setattr(jev.requests, "post", lambda *a, **k: _Resp(status=500, body={}))
    SCRIPTED_BACKEND.push({"text": json.dumps({"grade": 70, "notes": "ok"})})
    with app.app_context():
        jd.execute_run(stubbed_run)
        row = JobListing.query.one()
        assert row.graded_by == "llm" and row.match_grade == 70


def test_a_failed_llm_note_keeps_the_jev_grade(app, jev_on, stubbed_run, monkeypatch):
    monkeypatch.setattr(jd.adzuna, "search", lambda **kwargs: ([], 0, False))
    monkeypatch.setattr(jd.freehire, "search", lambda **kwargs: [_posting(1)])
    monkeypatch.setattr(jev.requests, "post", lambda *a, **k: _Resp(body={"answers": _answers()}))
    SCRIPTED_BACKEND.push({"text": "not json"})
    with app.app_context():
        jd.execute_run(stubbed_run)
        row = JobListing.query.one()
        assert row.graded_by == "jev" and row.match_grade >= 80
        assert "LLM note failed" in row.match_notes

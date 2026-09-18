"""app.services.job_sources.remotive -- the free, keyless Remotive
aggregator API. No live network: requests.get is monkeypatched, matching
the real shape confirmed live against remotive.com/api/remote-jobs.

This module makes exactly one HTTP call per invocation and has no daily-
budget logic of its own -- that lives in job_discovery.py (see
_today_remotive_calls/REMOTIVE_DAILY_CALL_LIMIT and
tests/test_job_discovery.py), since Remotive's own API response states a
real usage ceiling ("max. 4 times a day") that has to be tracked across
runs, not just within one search() call.
"""
import pytest

from app.services.job_sources import remotive


class _FakeResponse:
    def __init__(self, *, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _job(**overrides):
    job = {
        "id": 2069746,
        "url": "https://remotive.com/remote-jobs/software-development/tech-lead-2069746",
        "title": "Software Engineer",
        "company_name": "Acme",
        "category": "Software Development",
        "job_type": "full_time",
        "publication_date": "2026-09-14T20:33:27",
        "candidate_required_location": "USA Only",
        "salary": "",
        "description": "Build the thing.",
    }
    job.update(overrides)
    return job


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(remotive, "log_outbound", lambda *a, **k: None)


def test_search_normalizes_a_matching_job(monkeypatch):
    monkeypatch.setattr(
        remotive.requests, "get", lambda *a, **k: _FakeResponse(payload={"jobs": [_job()]})
    )
    listings = remotive.search(keywords=["Software Engineer"])
    assert len(listings) == 1
    listing = listings[0]
    assert listing["company_name"] == "Acme"
    assert listing["role_title"] == "Software Engineer"
    assert listing["location"] == "USA Only"
    assert listing["source"] == "Remotive"
    assert listing["date_posted"] is not None
    assert listing["_keyword"] == "Software Engineer"


def test_search_defaults_location_to_remote_when_blank(monkeypatch):
    monkeypatch.setattr(
        remotive.requests, "get",
        lambda *a, **k: _FakeResponse(payload={"jobs": [_job(candidate_required_location="")]}),
    )
    listings = remotive.search(keywords=["Software Engineer"])
    assert listings[0]["location"] == "Remote"


def test_search_filters_out_non_matching_titles(monkeypatch):
    monkeypatch.setattr(
        remotive.requests, "get",
        lambda *a, **k: _FakeResponse(payload={"jobs": [_job(title="Sales Manager")]}),
    )
    assert remotive.search(keywords=["Software Engineer"]) == []


def test_search_excludes_internship_titles(monkeypatch):
    monkeypatch.setattr(
        remotive.requests, "get",
        lambda *a, **k: _FakeResponse(payload={"jobs": [_job(title="Software Engineering Intern")]}),
    )
    assert remotive.search(keywords=["Software Engineer"]) == []


def test_search_handles_a_request_exception_gracefully(monkeypatch):
    monkeypatch.setattr(remotive.requests, "get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("boom")))
    assert remotive.search(keywords=["Software Engineer"]) == []

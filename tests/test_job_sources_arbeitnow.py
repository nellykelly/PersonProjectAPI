"""app.services.job_sources.arbeitnow -- the free, keyless Arbeitnow
aggregator API. No live network: requests.get is monkeypatched, matching
the real shape confirmed live against arbeitnow.com/api/job-board-api
(250 real listings, data/links/meta envelope, remote: bool per job,
created_at as unix epoch seconds).
"""
import pytest

from app.services.job_sources import arbeitnow


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
        "slug": "software-engineer-acme-123",
        "company_name": "Acme",
        "title": "Software Engineer",
        "description": "Build the thing.",
        "remote": True,
        "url": "https://www.arbeitnow.com/jobs/companies/acme/software-engineer-123",
        "tags": ["Python"],
        "job_types": ["Full Time"],
        "created_at": 1789612812,
    }
    job.update(overrides)
    return job


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(arbeitnow, "log_outbound", lambda *a, **k: None)


def test_search_returns_nothing_when_include_remote_is_false(monkeypatch):
    monkeypatch.setattr(
        arbeitnow.requests, "get",
        lambda *a, **k: _FakeResponse(payload={"data": [_job()]}),
    )
    assert arbeitnow.search(keywords=["Software Engineer"], include_remote=False) == []


def test_search_normalizes_a_matching_remote_job(monkeypatch):
    monkeypatch.setattr(
        arbeitnow.requests, "get",
        lambda *a, **k: _FakeResponse(payload={"data": [_job()]}),
    )
    listings = arbeitnow.search(keywords=["Software Engineer"], include_remote=True)
    assert len(listings) == 1
    listing = listings[0]
    assert listing["company_name"] == "Acme"
    assert listing["role_title"] == "Software Engineer"
    assert listing["location"] == "Remote"
    assert listing["source"] == "Arbeitnow"
    assert listing["date_posted"] is not None
    assert listing["_keyword"] == "Software Engineer"


def test_search_drops_non_remote_jobs(monkeypatch):
    monkeypatch.setattr(
        arbeitnow.requests, "get",
        lambda *a, **k: _FakeResponse(payload={"data": [_job(remote=False)]}),
    )
    assert arbeitnow.search(keywords=["Software Engineer"], include_remote=True) == []


def test_search_filters_out_non_matching_titles(monkeypatch):
    monkeypatch.setattr(
        arbeitnow.requests, "get",
        lambda *a, **k: _FakeResponse(payload={"data": [_job(title="Sales Manager")]}),
    )
    assert arbeitnow.search(keywords=["Software Engineer"], include_remote=True) == []


def test_search_excludes_internship_titles(monkeypatch):
    monkeypatch.setattr(
        arbeitnow.requests, "get",
        lambda *a, **k: _FakeResponse(payload={"data": [_job(title="Software Engineering Intern")]}),
    )
    assert arbeitnow.search(keywords=["Software Engineer"], include_remote=True) == []


def test_search_handles_a_request_exception_gracefully(monkeypatch):
    monkeypatch.setattr(arbeitnow.requests, "get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("boom")))
    assert arbeitnow.search(keywords=["Software Engineer"], include_remote=True) == []

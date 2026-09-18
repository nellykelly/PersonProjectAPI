"""app.services.job_sources.ashby -- Ashby's public per-company posting
API (api.ashbyhq.com), same shape/reasoning as Lever/Greenhouse. No live
network: requests.get is monkeypatched, matching the real shape confirmed
against api.ashbyhq.com/posting-api/job-board/<company> (notion, ramp,
mercury, linear, openai all verified live).
"""
import pytest

from app.services.job_sources import ashby


class _FakeResponse:
    def __init__(self, *, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _posting(**overrides):
    posting = {
        "id": "1fc309c8",
        "title": "Software Engineer",
        "location": "Remote",
        "jobUrl": "https://jobs.ashbyhq.com/acme/1fc309c8",
        "applyUrl": "https://jobs.ashbyhq.com/acme/1fc309c8/application",
        "publishedAt": "2026-08-24T14:44:49.699+00:00",
        "isRemote": True,
        "isListed": True,
        "department": "Engineering",
        "employmentType": "FullTime",
        "descriptionPlain": "Build the thing.",
    }
    posting.update(overrides)
    return posting


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(ashby, "log_outbound", lambda *a, **k: None)


def test_search_normalizes_a_matching_posting(monkeypatch):
    monkeypatch.setattr(
        ashby.requests, "get", lambda *a, **k: _FakeResponse(payload={"jobs": [_posting()]})
    )
    listings = ashby.search(company_slugs=["acme"], keywords=["Software Engineer"])
    assert len(listings) == 1
    listing = listings[0]
    assert listing["company_name"] == "Acme"
    assert listing["role_title"] == "Software Engineer"
    assert listing["location"] == "Remote"
    assert listing["job_posting_url"] == "https://jobs.ashbyhq.com/acme/1fc309c8"
    assert listing["source"] == "Ashby"
    assert listing["date_posted"] is not None
    assert listing["_keyword"] == "Software Engineer"


def test_search_skips_unlisted_postings(monkeypatch):
    monkeypatch.setattr(
        ashby.requests, "get",
        lambda *a, **k: _FakeResponse(payload={"jobs": [_posting(isListed=False)]}),
    )
    assert ashby.search(company_slugs=["acme"], keywords=["Software Engineer"]) == []


def test_search_filters_out_non_matching_titles(monkeypatch):
    monkeypatch.setattr(
        ashby.requests, "get",
        lambda *a, **k: _FakeResponse(payload={"jobs": [_posting(title="Account Executive")]}),
    )
    assert ashby.search(company_slugs=["acme"], keywords=["Software Engineer"]) == []


def test_search_excludes_internship_titles(monkeypatch):
    monkeypatch.setattr(
        ashby.requests, "get",
        lambda *a, **k: _FakeResponse(payload={"jobs": [_posting(title="Software Engineering Intern")]}),
    )
    assert ashby.search(company_slugs=["acme"], keywords=["Software Engineer"]) == []


def test_search_skips_a_bad_slug_without_failing_the_others(monkeypatch):
    def fake_get(url, **kwargs):
        if "broken" in url:
            return _FakeResponse(status_code=404, payload={})
        return _FakeResponse(payload={"jobs": [_posting()]})

    monkeypatch.setattr(ashby.requests, "get", fake_get)
    listings = ashby.search(company_slugs=["broken", "acme"], keywords=["Software Engineer"])
    assert len(listings) == 1


def test_search_handles_a_request_exception_gracefully(monkeypatch):
    monkeypatch.setattr(ashby.requests, "get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("boom")))
    assert ashby.search(company_slugs=["acme"], keywords=["Software Engineer"]) == []


def test_search_skips_blank_slugs():
    assert ashby.search(company_slugs=["", "  "], keywords=["Software Engineer"]) == []

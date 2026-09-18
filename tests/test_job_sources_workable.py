"""app.services.job_sources.workable -- Workable's public per-account
widget API (apply.workable.com), same shape/reasoning as Lever/Ashby/
Greenhouse. No live network: requests.get is monkeypatched. The endpoint
itself and its {name, description, jobs} envelope were verified live
against several real accounts; individual job field names (title,
shortlink, location.location_str, full_description) come from Workable's
own documented widget schema since none of the spot-checked accounts had
open postings at verification time to confirm against real populated data.
"""
import pytest

from app.services.job_sources import workable


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
        "title": "Software Engineer",
        "shortlink": "https://apply.workable.com/acme/j/ABC123/",
        "location": {"location_str": "Remote"},
        "published_on": "2026-08-24",
        "full_description": "Build the thing.",
    }
    job.update(overrides)
    return job


def _payload(*jobs, name="Acme"):
    return {"name": name, "description": None, "jobs": list(jobs)}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(workable, "log_outbound", lambda *a, **k: None)


def test_search_normalizes_a_matching_job(monkeypatch):
    monkeypatch.setattr(
        workable.requests, "get", lambda *a, **k: _FakeResponse(payload=_payload(_job()))
    )
    listings = workable.search(account_slugs=["acme"], keywords=["Software Engineer"])
    assert len(listings) == 1
    listing = listings[0]
    assert listing["company_name"] == "Acme"
    assert listing["role_title"] == "Software Engineer"
    assert listing["location"] == "Remote"
    assert listing["job_posting_url"] == "https://apply.workable.com/acme/j/ABC123/"
    assert listing["source"] == "Workable"
    assert listing["description"] == "Build the thing."
    assert listing["date_posted"] is not None
    assert listing["_keyword"] == "Software Engineer"


def test_search_falls_back_to_slug_when_name_is_missing(monkeypatch):
    monkeypatch.setattr(
        workable.requests, "get",
        lambda *a, **k: _FakeResponse(payload={"description": None, "jobs": [_job()]}),
    )
    listings = workable.search(account_slugs=["big-co"], keywords=["Software Engineer"])
    assert listings[0]["company_name"] == "Big Co"


def test_search_falls_back_to_url_when_shortlink_missing(monkeypatch):
    job = _job(url="https://apply.workable.com/acme/j/ABC123/apply")
    del job["shortlink"]
    monkeypatch.setattr(
        workable.requests, "get", lambda *a, **k: _FakeResponse(payload=_payload(job))
    )
    listings = workable.search(account_slugs=["acme"], keywords=["Software Engineer"])
    assert listings[0]["job_posting_url"] == "https://apply.workable.com/acme/j/ABC123/apply"


def test_search_filters_out_non_matching_titles(monkeypatch):
    monkeypatch.setattr(
        workable.requests, "get",
        lambda *a, **k: _FakeResponse(payload=_payload(_job(title="Account Executive"))),
    )
    assert workable.search(account_slugs=["acme"], keywords=["Software Engineer"]) == []


def test_search_excludes_internship_titles(monkeypatch):
    monkeypatch.setattr(
        workable.requests, "get",
        lambda *a, **k: _FakeResponse(payload=_payload(_job(title="Software Engineering Intern"))),
    )
    assert workable.search(account_slugs=["acme"], keywords=["Software Engineer"]) == []


def test_search_handles_empty_jobs_list(monkeypatch):
    monkeypatch.setattr(
        workable.requests, "get", lambda *a, **k: _FakeResponse(payload=_payload()),
    )
    assert workable.search(account_slugs=["acme"], keywords=["Software Engineer"]) == []


def test_search_skips_a_bad_slug_without_failing_the_others(monkeypatch):
    def fake_get(url, **kwargs):
        if "broken" in url:
            return _FakeResponse(status_code=404, payload={})
        return _FakeResponse(payload=_payload(_job()))

    monkeypatch.setattr(workable.requests, "get", fake_get)
    listings = workable.search(account_slugs=["broken", "acme"], keywords=["Software Engineer"])
    assert len(listings) == 1


def test_search_handles_a_request_exception_gracefully(monkeypatch):
    monkeypatch.setattr(workable.requests, "get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("boom")))
    assert workable.search(account_slugs=["acme"], keywords=["Software Engineer"]) == []


def test_search_skips_blank_slugs():
    assert workable.search(account_slugs=["", "  "], keywords=["Software Engineer"]) == []

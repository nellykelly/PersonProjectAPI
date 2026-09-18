"""app.services.job_sources.lever -- Lever's public per-company postings
API (api.lever.co), same shape/reasoning as Greenhouse's. No live network:
requests.get is monkeypatched to return a canned response, matching the
real shape confirmed against api.lever.co/v0/postings/<company>?mode=json.
"""
import pytest

from app.services.job_sources import lever


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
        "id": "abc123",
        "text": "Software Engineer",
        "hostedUrl": "https://jobs.lever.co/acme/abc123",
        "applyUrl": "https://jobs.lever.co/acme/abc123/apply",
        "createdAt": 1700000000000,
        "categories": {"location": "Remote", "commitment": "Full-time"},
        "descriptionPlain": "Build the thing.",
    }
    posting.update(overrides)
    return posting


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # log_outbound touches the DB (net_monitor) -- a no-op stub keeps this
    # a pure unit test of lever.search()'s own parsing/filtering logic.
    monkeypatch.setattr(lever, "log_outbound", lambda *a, **k: None)


def test_search_normalizes_a_matching_posting(monkeypatch):
    monkeypatch.setattr(lever.requests, "get", lambda *a, **k: _FakeResponse(payload=[_posting()]))

    listings = lever.search(company_slugs=["acme"], keywords=["Software Engineer"])
    assert len(listings) == 1
    listing = listings[0]
    assert listing["company_name"] == "Acme"
    assert listing["role_title"] == "Software Engineer"
    assert listing["location"] == "Remote"
    assert listing["job_posting_url"] == "https://jobs.lever.co/acme/abc123"
    assert listing["source"] == "Lever"
    assert listing["description"] == "Build the thing."
    assert listing["date_posted"] is not None
    assert listing["_keyword"] == "Software Engineer"


def test_search_filters_out_non_matching_titles(monkeypatch):
    monkeypatch.setattr(
        lever.requests, "get",
        lambda *a, **k: _FakeResponse(payload=[_posting(text="Sales Development Representative")]),
    )
    listings = lever.search(company_slugs=["acme"], keywords=["Software Engineer"])
    assert listings == []


def test_search_excludes_internship_titles(monkeypatch):
    monkeypatch.setattr(
        lever.requests, "get",
        lambda *a, **k: _FakeResponse(payload=[_posting(text="Software Engineering Intern")]),
    )
    listings = lever.search(company_slugs=["acme"], keywords=["Software Engineer"])
    assert listings == []


def test_search_skips_a_bad_slug_without_failing_the_others(monkeypatch):
    def fake_get(url, **kwargs):
        if "broken" in url:
            return _FakeResponse(status_code=404, payload={"ok": False})
        return _FakeResponse(payload=[_posting()])

    monkeypatch.setattr(lever.requests, "get", fake_get)
    listings = lever.search(company_slugs=["broken", "acme"], keywords=["Software Engineer"])
    assert len(listings) == 1
    assert listings[0]["company_name"] == "Acme"


def test_search_handles_a_request_exception_gracefully(monkeypatch):
    def fake_get(url, **kwargs):
        raise ConnectionError("boom")

    monkeypatch.setattr(lever.requests, "get", fake_get)
    listings = lever.search(company_slugs=["acme"], keywords=["Software Engineer"])
    assert listings == []


def test_search_skips_blank_slugs():
    listings = lever.search(company_slugs=["", "  "], keywords=["Software Engineer"])
    assert listings == []


def test_company_label_derives_a_readable_name_from_the_slug():
    assert lever._company_label("gitlab-inc") == "Gitlab Inc"
    assert lever._company_label("acme_corp") == "Acme Corp"


def test_parse_date_handles_missing_and_bad_input():
    assert lever._parse_date(None) is None
    assert lever._parse_date("not-a-number") is None
    assert lever._parse_date(1700000000000) is not None

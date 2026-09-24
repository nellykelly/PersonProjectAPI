"""app.services.job_sources.freehire -- the keyless freehire.me aggregator
API. No live network: requests.get is monkeypatched with the response
shape confirmed live against /api/v1/agent/jobs/search on 2026-09-23."""
import pytest

from app.services.job_sources import dedupe_key, freehire


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
        "public_slug": "ai-engineer-acme-abc123",
        "url": "https://jobs.example.com/acme/1?utm_source=freehire.me",
        "title": "AI Engineer",
        "company": "Acme",
        "location": "Houston, Texas, United States",
        "work_mode": "onsite",
        "description": "Build agents in Python.",
        "posted_at": "2026-09-20T12:00:00Z",
        "enrichment": {"salary_min": 150000, "salary_max": 190000, "salary_currency": "USD"},
    }
    job.update(overrides)
    return job


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(freehire, "log_outbound", lambda *a, **k: None)


def _serve(monkeypatch, jobs, calls=None):
    def fake_get(url, params=None, timeout=None):
        if calls is not None:
            calls.append(params)
        return _FakeResponse(payload={"data": jobs})

    monkeypatch.setattr(freehire.requests, "get", fake_get)


def test_search_normalizes_a_matching_job(app, monkeypatch):
    calls = []
    _serve(monkeypatch, [_job()], calls)
    with app.app_context():
        listings = freehire.search(keywords=["AI Engineer"], locations=["Houston, TX"], include_remote=False)
    assert len(listings) == 1
    listing = listings[0]
    assert listing["company_name"] == "Acme"
    assert listing["source"] == "freehire"
    assert listing["salary_range"] == "$150,000 - $190,000"
    assert str(listing["date_posted"]) == "2026-09-20"
    assert listing["_keyword"] == "AI Engineer"
    assert ("q", "AI Engineer") in calls[0] and ("countries", "US") in calls[0]


def test_search_filters_by_city_but_keeps_remote_when_enabled(app, monkeypatch):
    jobs = [
        _job(url="https://x/1", location="Austin, Texas, United States"),
        _job(url="https://x/2", location="United States", work_mode="remote"),
        _job(url="https://x/3", location="San Francisco, California, United States"),
    ]
    _serve(monkeypatch, jobs)
    with app.app_context():
        listings = freehire.search(
            keywords=["AI Engineer"], locations=["San Francisco Bay Area, CA"], include_remote=True
        )
    assert [l["job_posting_url"] for l in listings] == ["https://x/2", "https://x/3"]
    assert listings[0]["location"] == "Remote (United States)"


def test_search_drops_remote_when_remote_is_off(app, monkeypatch):
    _serve(monkeypatch, [_job(location="United States", work_mode="remote")])
    with app.app_context():
        assert freehire.search(keywords=["AI Engineer"], locations=["Houston, TX"], include_remote=False) == []


def test_search_requires_a_title_match_and_excludes_internships(app, monkeypatch):
    _serve(monkeypatch, [
        _job(url="https://x/1", title="Sales Engineer"),
        _job(url="https://x/2", title="AI Engineer Intern"),
    ])
    with app.app_context():
        assert freehire.search(keywords=["AI Engineer"], locations=[], include_remote=True) == []


def test_search_handles_a_request_exception_gracefully(app, monkeypatch):
    monkeypatch.setattr(
        freehire.requests, "get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("boom"))
    )
    with app.app_context():
        assert freehire.search(keywords=["AI Engineer"], locations=[], include_remote=True) == []


def test_dedupe_key_ignores_case_punctuation_and_legal_suffixes():
    assert dedupe_key("Acme, Inc.", "Software Engineer (Backend)") == dedupe_key(
        "acme", "software engineer - backend"
    )
    assert dedupe_key("Acme", "Software Engineer") != dedupe_key("Acme", "Senior Software Engineer")

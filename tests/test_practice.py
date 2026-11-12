"""/practice -- the password-gated ATL-101 practice sandbox proxy.

The sandbox service itself (sandbox/) isn't running in the suite: sandbox.forward is
replaced with a recorder, so these tests cover the gate, the per-browser sandbox id, the
CSRF-header rule and what gets forwarded, not dbt.
"""
import pytest
from werkzeug.security import generate_password_hash

from app.services import practice_sandbox

PASSWORD = "correct-horse-battery-staple"
HASH_KEY = "PRACTICE_PASSWORD_HASH"


@pytest.fixture()
def calls(monkeypatch):
    seen = []

    def fake_forward(base_url, method, path, query, headers, body, sandbox_id, timeout):
        seen.append({"method": method, "path": path, "query": dict(query), "body": body,
                     "sandbox_id": sandbox_id, "x_sandbox": headers.get("X-Sandbox")})
        if path == "index.html":
            return 200, {"Content-Type": "text/html; charset=utf-8"}, b"<title>ATL-101 Practice Sandbox</title>"
        return 200, {"Content-Type": "application/json"}, b'{"ok": true}'

    monkeypatch.setattr(practice_sandbox, "forward", fake_forward)
    return seen


@pytest.fixture()
def locked(app, client, calls):
    app.config[HASH_KEY] = generate_password_hash(PASSWORD)
    yield client
    app.config[HASH_KEY] = None


@pytest.fixture()
def unlocked(locked):
    locked.post("/practice/", data={"password": PASSWORD})
    return locked


def test_no_password_hash_is_committed_to_the_repo():
    from app.config import Config

    assert getattr(Config, HASH_KEY) in (None, "")


@pytest.mark.parametrize("path", ["/practice/", "/practice/app/", "/practice/app/api/state"])
def test_fails_closed_when_unconfigured(app, client, calls, path):
    app.config[HASH_KEY] = None
    resp = client.get(path)
    assert resp.status_code in (302, 401, 503)
    assert resp.status_code != 200
    assert calls == []


def test_locked_shows_gate_and_proxies_nothing(locked, calls):
    gate = locked.get("/practice/")
    assert gate.status_code == 200 and b'type="password"' in gate.data
    assert locked.get("/practice/app/").status_code == 302
    assert locked.get("/practice/app/api/state").status_code == 401
    assert locked.post("/practice/app/api/sql", json={"sql": "select 1"},
                       headers={"X-Sandbox": "1"}).status_code == 401
    assert calls == []


def test_wrong_password_is_401(locked):
    resp = locked.post("/practice/", data={"password": "nope"})
    assert resp.status_code == 401
    assert b"not right" in resp.data


def test_correct_password_opens_the_app(unlocked, calls):
    page = unlocked.get("/practice/app/")
    assert page.status_code == 200
    assert b"ATL-101" in page.data
    assert calls[-1]["path"] == "index.html"
    assert unlocked.get("/practice/").headers["Location"].endswith("/practice/app/")


def test_each_browser_gets_its_own_sandbox_id(app, calls):
    app.config[HASH_KEY] = generate_password_hash(PASSWORD)
    first, second = app.test_client(), app.test_client()
    for c in (first, second):
        c.post("/practice/", data={"password": PASSWORD})
        c.get("/practice/app/api/state")
    ids = [call["sandbox_id"] for call in calls]
    assert len(ids) == 2 and ids[0] != ids[1]
    assert all(practice_sandbox.valid_sandbox_id(i) for i in ids)
    app.config[HASH_KEY] = None


def test_sandbox_id_survives_lock_and_unlock(unlocked, calls):
    unlocked.get("/practice/app/api/state")
    unlocked.get("/practice/lock")
    assert unlocked.get("/practice/app/api/state").status_code == 401
    unlocked.post("/practice/", data={"password": PASSWORD})
    unlocked.get("/practice/app/api/state")
    assert calls[0]["sandbox_id"] == calls[-1]["sandbox_id"]


def test_post_without_x_sandbox_header_is_refused(unlocked, calls):
    resp = unlocked.post("/practice/app/api/sql", json={"sql": "select 1"})
    assert resp.status_code == 403
    assert calls == []


def test_post_is_forwarded_with_body_and_query(unlocked, calls):
    resp = unlocked.post("/practice/app/api/sql", json={"sql": "select 1"}, headers={"X-Sandbox": "1"})
    assert resp.status_code == 200
    assert calls[-1]["method"] == "POST" and calls[-1]["path"] == "api/sql"
    assert b"select 1" in calls[-1]["body"]
    unlocked.get("/practice/app/api/file?path=models/x.sql")
    assert calls[-1]["query"] == {"path": "models/x.sql"}


def test_oversized_post_is_413(unlocked, calls):
    resp = unlocked.post("/practice/app/api/sql", data=b"x" * (practice_sandbox.MAX_BODY + 1),
                         headers={"X-Sandbox": "1", "Content-Type": "application/json"})
    assert resp.status_code == 413
    assert calls == []


def test_sandbox_down_is_503_not_500(unlocked, monkeypatch):
    def down(*args, **kwargs):
        raise practice_sandbox.SandboxUnavailable("connection refused")

    monkeypatch.setattr(practice_sandbox, "forward", down)
    assert unlocked.get("/practice/app/api/state").status_code == 503
    page = unlocked.get("/practice/app/")
    assert page.status_code == 503 and b"offline" in page.data


def test_every_response_is_noindex(unlocked):
    for path in ("/practice/", "/practice/app/", "/practice/app/api/state"):
        resp = unlocked.get(path)
        assert resp.headers["X-Robots-Tag"] == "noindex, nofollow"
        assert "no-store" in resp.headers["Cache-Control"]


def test_robots_txt_disallows_practice(client):
    assert b"Disallow: /practice" in client.get("/robots.txt").data


def test_practice_is_not_in_the_sitemap(client):
    assert b"/practice" not in client.get("/sitemap.xml").data

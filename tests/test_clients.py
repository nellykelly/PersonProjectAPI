"""The /clients/<slug> password gate -- same fail-closed model as /family."""
import pytest
from werkzeug.security import generate_password_hash

PASSWORD = "correct-horse-battery-staple"
URL = "/clients/espressocaribbean"
HASH_KEY = "CLIENT_ESPRESSOCARIBBEAN_PASSWORD_HASH"


@pytest.fixture()
def locked_client(app, client):
    app.config[HASH_KEY] = generate_password_hash(PASSWORD)
    yield client
    app.config[HASH_KEY] = None


def test_no_password_hash_is_committed_to_the_repo():
    from app.config import Config

    assert getattr(Config, HASH_KEY) in (None, "")


def test_fails_closed_when_unconfigured(app, client):
    app.config[HASH_KEY] = None
    resp = client.get(URL)
    assert resp.status_code == 503
    assert b"File map" not in resp.data
    assert client.post(URL, data={"password": ""}).status_code == 503


def test_unknown_client_is_404(locked_client):
    assert locked_client.get("/clients/nobody").status_code == 404


def test_locked_shows_gate_not_content(locked_client):
    resp = locked_client.get(URL)
    assert resp.status_code == 200
    assert b'type="password"' in resp.data
    assert b"File map" not in resp.data


def test_wrong_password_is_401(locked_client):
    resp = locked_client.post(URL, data={"password": "nope"})
    assert resp.status_code == 401
    assert b"not right" in resp.data
    assert b"File map" not in resp.data


def test_correct_password_unlocks_and_persists(locked_client):
    resp = locked_client.post(URL, data={"password": PASSWORD})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(URL)
    page = locked_client.get(URL)
    assert page.status_code == 200
    assert b"File map" in page.data


def test_lock_re_gates(locked_client):
    locked_client.post(URL, data={"password": PASSWORD})
    locked_client.get(URL + "/lock")
    assert b"File map" not in locked_client.get(URL).data


def test_every_response_is_noindex_and_uncached(locked_client):
    resp = locked_client.get(URL)
    assert resp.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert "no-store" in resp.headers["Cache-Control"]
    assert b'name="robots" content="noindex' in resp.data


def test_robots_txt_disallows_clients(client):
    assert b"Disallow: /clients" in client.get("/robots.txt").data

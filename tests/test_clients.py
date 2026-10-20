"""The /clients/<slug>/ password-gated client sites -- same fail-closed
model as /family, applied to every file of the static export."""
import pytest
from werkzeug.security import generate_password_hash

PASSWORD = "correct-horse-battery-staple"
URL = "/clients/espressocaribbean/"
HASH_KEY = "CLIENT_ESPRESSOCARIBBEAN_PASSWORD_HASH"
SITE_MARKER = b"/clients/espressocaribbean/_next/static/"


@pytest.fixture()
def locked_client(app, client):
    app.config[HASH_KEY] = generate_password_hash(PASSWORD)
    yield client
    app.config[HASH_KEY] = None


@pytest.fixture()
def unlocked_client(locked_client):
    locked_client.post(URL, data={"password": PASSWORD})
    return locked_client


def test_no_password_hash_is_committed_to_the_repo():
    from app.config import Config

    assert getattr(Config, HASH_KEY) in (None, "")


@pytest.mark.parametrize("path", ["", "recipes/", "maplibre-gl-worker.mjs"])
def test_fails_closed_when_unconfigured(app, client, path):
    app.config[HASH_KEY] = None
    resp = client.get(URL + path)
    assert resp.status_code == 503
    assert SITE_MARKER not in resp.data


def test_unknown_client_is_404(locked_client):
    assert locked_client.get("/clients/nobody/").status_code == 404


def test_bare_slug_redirects_to_trailing_slash(locked_client):
    resp = locked_client.get("/clients/espressocaribbean")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(URL)


def test_locked_shows_gate_not_site(locked_client):
    resp = locked_client.get(URL)
    assert resp.status_code == 200
    assert b'type="password"' in resp.data
    assert SITE_MARKER not in resp.data


@pytest.mark.parametrize("path", ["recipes/", "maplibre-gl-worker.mjs", "qr/recipe-01.svg"])
def test_locked_pages_and_assets_redirect_to_gate(locked_client, path):
    resp = locked_client.get(URL + path)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(URL)


def test_wrong_password_is_401(locked_client):
    resp = locked_client.post(URL, data={"password": "nope"})
    assert resp.status_code == 401
    assert b"not right" in resp.data


def test_correct_password_serves_the_site(unlocked_client):
    page = unlocked_client.get(URL)
    assert page.status_code == 200
    assert SITE_MARKER in page.data


@pytest.mark.parametrize("path", ["recipes/", "recipes", "recipes/recipe-01/"])
def test_trailing_slash_layout_resolves(unlocked_client, path):
    assert unlocked_client.get(URL + path).status_code == 200


def test_map_worker_is_served_as_javascript(unlocked_client):
    resp = unlocked_client.get(URL + "maplibre-gl-worker.mjs")
    assert resp.status_code == 200
    assert resp.mimetype == "text/javascript"


def test_missing_file_is_404_and_traversal_blocked(unlocked_client):
    assert unlocked_client.get(URL + "nope/").status_code == 404
    assert unlocked_client.get(URL + "../../config.py").status_code == 404
    assert unlocked_client.get(URL + "..%2F..%2Fconfig.py").status_code == 404


def test_lock_re_gates(unlocked_client):
    unlocked_client.get(URL + "lock")
    assert SITE_MARKER not in unlocked_client.get(URL).data


def test_every_response_is_noindex(unlocked_client):
    for path in ("", "maplibre-gl-worker.mjs"):
        assert unlocked_client.get(URL + path).headers["X-Robots-Tag"] == "noindex, nofollow"
    assert "no-store" in unlocked_client.get(URL).headers["Cache-Control"]


def test_robots_txt_disallows_clients(client):
    assert b"Disallow: /clients" in client.get("/robots.txt").data

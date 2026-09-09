"""The /family password gate -- same fail-closed model as /job-tracker."""
import pytest
from werkzeug.security import generate_password_hash

from app import models
from app.extensions import db

PASSWORD = "correct-horse-battery-staple"
GATED = ("/family/home", "/family/calendar", "/family/grocery", "/family/chat")


@pytest.fixture()
def locked_client(app, client):
    app.config["FAMILY_PASSWORD_HASH"] = generate_password_hash(PASSWORD)
    with app.app_context():
        db.create_all()
        db.session.add_all([
            models.FamilyMember(slug="m1", name="Nelson", accent="leaf"),
            models.FamilyMember(slug="m2", name="Savannah", accent="bloom"),
        ])
        db.session.commit()
    yield client
    app.config["FAMILY_PASSWORD_HASH"] = None


@pytest.fixture()
def unlocked_client(locked_client):
    locked_client.post("/family/unlock", data={"password": PASSWORD})
    return locked_client


def test_no_password_hash_is_committed_to_the_repo():
    from app.config import Config

    assert Config.FAMILY_PASSWORD_HASH in (None, "")


@pytest.mark.parametrize("path", GATED + ("/family",))
def test_fails_closed_when_unconfigured(app, client, path):
    app.config["FAMILY_PASSWORD_HASH"] = None
    assert client.get(path).status_code == 503


@pytest.mark.parametrize("path", GATED)
def test_locked_routes_redirect_to_the_gate(locked_client, path):
    resp = locked_client.get(path)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/family")


def test_wrong_password_is_401(locked_client):
    resp = locked_client.post("/family/unlock", data={"password": "nope"})
    assert resp.status_code == 401
    assert b"not right" in resp.data


def test_correct_password_unlocks_and_persists(unlocked_client):
    r = unlocked_client.get("/family/home")
    assert r.status_code == 200
    assert b"fam-tabs" in r.data          # the shell rendered
    # a second request still works (session flag persisted)
    assert unlocked_client.get("/family/calendar").status_code == 200


def test_lock_re_gates(unlocked_client):
    unlocked_client.get("/family/lock")
    assert unlocked_client.get("/family/home").status_code == 302


def test_every_family_response_is_noindex(unlocked_client):
    assert unlocked_client.get("/family/home").headers["X-Robots-Tag"] == "noindex, nofollow"


def test_robots_disallows_family(client):
    assert b"Disallow: /family" in client.get("/robots.txt").data


def test_sitemap_excludes_family(client):
    assert b"/family" not in client.get("/sitemap.xml").data


def test_not_linked_from_the_main_nav(client):
    assert b"/family" not in client.get("/about").data


def test_family_404_uses_the_family_theme(unlocked_client):
    b = unlocked_client.get("/family/nowhere").data
    assert unlocked_client.get("/family/nowhere").status_code == 404
    assert b"family.css" in b
    assert b"base.js" not in b and b"main.css" not in b   # no Dimension chrome


def test_family_templates_do_not_load_the_dimension_theme(unlocked_client):
    for path in GATED:
        b = unlocked_client.get(path).data
        assert b"css/custom.css" not in b
        assert b"assets/css/main.css" not in b
        assert b"js/base.js" not in b

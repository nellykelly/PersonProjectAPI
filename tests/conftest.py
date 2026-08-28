import pytest

from app import create_app
from app.extensions import db as _db


@pytest.fixture
def app():
    application = create_app("testing")
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    with app.app_context():
        yield _db


@pytest.fixture
def make_user(app):
    """Factory: make_user("name") -> commits a User and returns its username.

    Uses its own short-lived app context on purpose (not the `db` fixture):
    a `db` context held open across the test's HTTP requests would make
    Flask-Login cache the first request's user in `g` and reuse it for
    every later request, which breaks any test that logs in as more than
    one account.
    """
    from app.models import User

    def _make(username="tester", password="password123"):
        with app.app_context():
            user = User()
            user.set_username(username)
            user.set_password(password)
            _db.session.add(user)
            _db.session.commit()
            return user.username

    return _make


@pytest.fixture
def login(app):
    """Factory: login("name") -> a fresh test client logged in as that user."""

    def _login(username, password="password123"):
        c = app.test_client()
        resp = c.post(
            "/auth/login", data={"username": username, "password": password}
        )
        assert resp.status_code == 302, resp.data
        return c

    return _login


@pytest.fixture
def auth_client(make_user, login):
    """A test client logged in as a fresh user named 'tester'."""
    return login(make_user())

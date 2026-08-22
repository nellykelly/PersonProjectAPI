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

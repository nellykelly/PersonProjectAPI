"""app/services/assistant/rate_limit.py -- the shared bucket that keeps an
assistant tool from getting extra quota beyond what its equivalent web route
allows. Calls `limiter.limiter.hit()` directly (bypassing the route-level
decorator), so these hits count for real even under TestingConfig's
RATELIMIT_ENABLED = False -- except consume() short-circuits to "always
allowed" in that case, exactly like flask_limiter's own decorated routes do
when rate limiting is administratively disabled. That short-circuit is what
lets every other caller of consume() (trading, pipeline world, scorer,
timed-squares tools) use the plain `app` fixture from conftest.py with no
special setup.
"""
import pytest

from app import create_app
from app.extensions import limiter
from app.services.assistant import rate_limit


@pytest.fixture
def rate_limited_app():
    """A testing app with rate limiting actually enabled.

    TestingConfig sets RATELIMIT_ENABLED = False so route tests elsewhere
    don't trip limits incidentally -- but that also makes flask-limiter's
    `init_app` return before building `_limiter`/`_storage` at all (see
    flask_limiter.extension.Limiter.init_app). consume() itself now handles
    that case by short-circuiting to True (see
    test_consume_is_a_noop_when_rate_limiting_is_disabled below), so this
    fixture exists only for the tests here that need to exercise the real
    allow/deny bucket behavior -- flip the flag back on and re-run init_app
    (safe to call more than once; it just rebuilds storage) so `limiter.limiter`
    has a real storage backend to hit against.
    """
    application = create_app("testing")
    application.config["RATELIMIT_ENABLED"] = True
    limiter.init_app(application)
    yield application


def test_call_under_the_limit_is_allowed(rate_limited_app):
    with rate_limited_app.test_request_context():
        assert rate_limit.consume("test-under", "2 per hour") is True


def test_third_call_over_a_two_per_hour_limit_is_denied(rate_limited_app):
    with rate_limited_app.test_request_context():
        assert rate_limit.consume("test-exceed", "2 per hour") is True
        assert rate_limit.consume("test-exceed", "2 per hour") is True
        assert rate_limit.consume("test-exceed", "2 per hour") is False


def test_different_actions_get_independent_buckets(rate_limited_app):
    with rate_limited_app.test_request_context():
        assert rate_limit.consume("action-a", "2 per hour") is True
        assert rate_limit.consume("action-a", "2 per hour") is True
        # action-a is now exhausted, but action-b shares the same limit
        # string and IP -- it must not be affected by action-a's usage.
        assert rate_limit.consume("action-b", "2 per hour") is True
        assert rate_limit.consume("action-b", "2 per hour") is True
        assert rate_limit.consume("action-a", "2 per hour") is False
        assert rate_limit.consume("action-b", "2 per hour") is False


def test_different_limit_strings_do_not_cross_contaminate_the_parse_cache(rate_limited_app):
    with rate_limited_app.test_request_context():
        # Exhaust a 2/hour bucket, then confirm a 5/hour bucket (parsed
        # after the 2/hour one, exercising the cache) still allows its own
        # full quota independently.
        assert rate_limit.consume("small-quota", "2 per hour") is True
        assert rate_limit.consume("small-quota", "2 per hour") is True
        assert rate_limit.consume("small-quota", "2 per hour") is False

        for _ in range(5):
            assert rate_limit.consume("big-quota", "5 per hour") is True
        assert rate_limit.consume("big-quota", "5 per hour") is False


def test_consume_is_a_noop_when_rate_limiting_is_disabled(app):
    """The test proving the fix: the plain `app` fixture from conftest.py
    (TestingConfig, RATELIMIT_ENABLED = False, limiter._limiter/_storage
    never built) must NOT raise when consume() is called through it -- it
    should behave like a decorated route with rate limiting disabled and
    just allow the call, every time, without ever touching limiter.limiter.
    """
    with app.test_request_context():
        assert app.config["RATELIMIT_ENABLED"] is False
        for _ in range(5):
            assert rate_limit.consume("disabled-action", "1 per hour") is True

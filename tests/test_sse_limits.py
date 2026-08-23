"""app/services/sse_limits.py -- the concurrency caps that stand between
one visitor scripting a handful of SSE connections and the whole site's
gunicorn thread pool hanging for everyone else. Tests run against the
same fakeredis connection app/services/queue.py already sets up under
TESTING, so these are real INCR/DECR/EXPIRE calls, not mocked counters.
"""
import pytest

from app.services import sse_limits


def test_acquire_then_release_returns_counters_to_zero(app):
    with app.app_context():
        sse_limits.acquire_sse_slot("test-cat", "1.2.3.4")
        sse_limits.release_sse_slot("test-cat", "1.2.3.4")

        conn = sse_limits.queue_service.get_redis_connection()
        assert int(conn.get("sse:global:total") or 0) == 0
        assert int(conn.get("sse:test-cat:total") or 0) == 0
        assert int(conn.get("sse:test-cat:client:1.2.3.4") or 0) == 0


def test_per_client_cap_is_enforced(app):
    with app.app_context():
        sse_limits.acquire_sse_slot("test-cat", "1.1.1.1", max_per_client=2)
        sse_limits.acquire_sse_slot("test-cat", "1.1.1.1", max_per_client=2)

        with pytest.raises(sse_limits.TooManyConnections):
            sse_limits.acquire_sse_slot("test-cat", "1.1.1.1", max_per_client=2)

        # A different client is unaffected -- the cap is per-client, not global.
        sse_limits.acquire_sse_slot("test-cat", "2.2.2.2", max_per_client=2)


def test_per_category_cap_is_enforced_across_clients(app):
    with app.app_context():
        sse_limits.acquire_sse_slot("cat-a", "1.1.1.1", max_per_category=2, max_per_client=5)
        sse_limits.acquire_sse_slot("cat-a", "2.2.2.2", max_per_category=2, max_per_client=5)

        with pytest.raises(sse_limits.TooManyConnections):
            sse_limits.acquire_sse_slot("cat-a", "3.3.3.3", max_per_category=2, max_per_client=5)

        # A different category has its own budget.
        sse_limits.acquire_sse_slot("cat-b", "1.1.1.1", max_per_category=2, max_per_client=5)


def test_global_cap_is_enforced_across_categories(app):
    with app.app_context():
        sse_limits.acquire_sse_slot("cat-a", "1.1.1.1", max_global=2, max_per_category=5, max_per_client=5)
        sse_limits.acquire_sse_slot("cat-b", "2.2.2.2", max_global=2, max_per_category=5, max_per_client=5)

        with pytest.raises(sse_limits.TooManyConnections):
            sse_limits.acquire_sse_slot("cat-c", "3.3.3.3", max_global=2, max_per_category=5, max_per_client=5)


def test_failed_acquire_rolls_back_every_counter_it_touched(app):
    with app.app_context():
        conn = sse_limits.queue_service.get_redis_connection()

        with pytest.raises(sse_limits.TooManyConnections):
            sse_limits.acquire_sse_slot("test-cat", "1.1.1.1", max_per_client=0)

        # The failed attempt should leave no trace -- every counter it
        # incremented on the way to the cap it failed should be rolled
        # back, not just the one that tripped the limit.
        assert int(conn.get("sse:global:total") or 0) == 0
        assert int(conn.get("sse:test-cat:total") or 0) == 0
        assert int(conn.get("sse:test-cat:client:1.1.1.1") or 0) == 0


def test_release_frees_a_slot_for_a_new_connection(app):
    with app.app_context():
        sse_limits.acquire_sse_slot("test-cat", "1.1.1.1", max_per_client=1)
        with pytest.raises(sse_limits.TooManyConnections):
            sse_limits.acquire_sse_slot("test-cat", "1.1.1.1", max_per_client=1)

        sse_limits.release_sse_slot("test-cat", "1.1.1.1")
        sse_limits.acquire_sse_slot("test-cat", "1.1.1.1", max_per_client=1)  # should not raise now


# ---------- route-level: the 429 path, without ever touching the infinite generator ----------


def test_watchlist_stream_returns_429_once_at_cap(client, app):
    with app.app_context():
        # Exhaust the *category* cap using distinct client IPs (not the
        # test client's own "127.0.0.1") so this trips the category limit
        # specifically, not the per-client one.
        for i in range(sse_limits.CATEGORY_SSE_CAP):
            sse_limits.acquire_sse_slot("watchlist", f"9.9.9.{i}")

    resp = client.get("/projects/trading-simulator/api/watchlist/stream")
    assert resp.status_code == 429
    assert resp.get_json()["ok"] is False


def test_risk_feed_returns_429_once_at_cap(client, app, db):
    from datetime import date, timedelta

    from app.models import Leg, Strategy
    from app.services import instruments

    with app.app_context():
        instrument = instruments.get_or_create_instrument("AAPL", "stock")
        strategy = Strategy(session_id="s1", name="Single Leg")
        leg = Leg(strategy=strategy, instrument=instrument, side="buy", quantity=1, entry_price=100.0)
        db.session.add(strategy)
        db.session.add(leg)
        db.session.commit()
        leg_id = leg.id

        for i in range(sse_limits.CATEGORY_SSE_CAP):
            sse_limits.acquire_sse_slot("risk-feed", f"9.9.9.{i}")

    resp = client.get(f"/projects/trading-simulator/api/positions/{leg_id}/risk-feed")
    assert resp.status_code == 429


# There used to be a third SSE consumer here (the Network Sniffer's live
# stream). It's now a polled analytics board instead (see net_monitor.py),
# with no open connection and so nothing for this module's concurrency
# cap to protect -- removed along with that route rather than kept
# testing a stream that no longer exists.

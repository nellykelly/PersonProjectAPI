"""app/services/analytics.py is intentionally Postgres-only (see its
module docstring). This sandbox runs tests against SQLite, so the real,
deterministic coverage here is the *fallback* behavior -- every metric
must fail cleanly with a clear message rather than crash or silently
return garbage when there's no Postgres connection.

The metrics' actual SQL *correctness* (does the window function compute
the right rolling average, etc.) needs a live Postgres to verify and is
gated behind `_postgres_available()`, auto-skipping here and running for
real once the app is pointed at the docker-compose postgres service.
"""
import pytest

from app.extensions import db
from app.services import analytics


def _postgres_available() -> bool:
    try:
        return db.engine.dialect.name == "postgresql"
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="requires a live Postgres connection -- run via `docker compose up` with DATABASE_URL set to it",
)


def test_is_postgres_is_false_on_the_sqlite_test_database(app):
    with app.app_context():
        assert analytics.is_postgres() is False


@pytest.mark.parametrize("metric_key", list(analytics.METRICS.keys()))
def test_every_metric_raises_a_clean_unavailable_error_on_non_postgres(app, metric_key):
    with app.app_context():
        with pytest.raises(analytics.AnalyticsUnavailable, match="Postgres"):
            analytics.run_metric(metric_key)


def test_run_all_metrics_reports_a_per_metric_error_without_raising(app):
    with app.app_context():
        results = analytics.run_all_metrics()

    assert set(results.keys()) == set(analytics.METRICS.keys())
    for entry in results.values():
        assert entry["rows"] is None
        assert entry["error"] is not None
        assert "sql" in entry and entry["sql"]  # the actual query is always shown, even on failure


# ---------- Postgres-only correctness (gated) ----------


@requires_postgres
def test_appearance_duplication_counts_matches_a_manual_group_by(app, db):
    """A real integration check once Postgres is available -- not skipped
    in principle, just skipped in this sandbox."""
    from app.models import Character

    db.session.add_all(
        [
            Character(session_id="s1", first_name="A", last_name="One", appearance_id="sky", status="live"),
            Character(session_id="s2", first_name="B", last_name="Two", appearance_id="sky", status="live"),
            Character(session_id="s3", first_name="C", last_name="Three", appearance_id="rose", status="live"),
        ]
    )
    db.session.commit()

    rows = {r["appearance_id"]: r["character_count"] for r in analytics.run_metric("appearance_duplication_counts")}
    assert rows["sky"] == 2
    assert rows["rose"] == 1

"""The SQL showcase for Pipeline World: real, hand-written SQL over the
`pipeline_runs` table -- joins, GROUP BY aggregations, and window
functions, not ORM model-by-model iteration.

**Postgres-only, by design and by necessity.** `DATE_TRUNC` and the
window-function frame syntax used below are Postgres dialect; this
matters because the whole point of this page is showing real,
non-trivial SQL, and rewriting it in a database-agnostic subset would
mean either dropping the window functions entirely or faking them with
Python loops -- exactly the "not fake" bar this project is held to.
Everywhere else in this app, models work against SQLite too (see
models.py); this module specifically requires a live Postgres
connection. See docs/build-spec (project-3-4) and this project's
README for why Postgres was chosen here over the rest of the site's
SQLite default.
"""
from __future__ import annotations

from sqlalchemy import text

from app.extensions import db

SUCCESS_RATE_OVER_TIME_SQL = """
    SELECT
        DATE_TRUNC('day', started_at)::date AS day,
        COUNT(*) FILTER (WHERE status = 'pass')::float / COUNT(*) AS pass_rate,
        COUNT(*) AS total_runs
    FROM pipeline_runs
    GROUP BY 1
    ORDER BY 1
"""

MEAN_TIME_BETWEEN_FAILURES_SQL = """
    WITH failures AS (
        SELECT
            started_at,
            started_at - LAG(started_at) OVER (ORDER BY started_at) AS gap
        FROM pipeline_runs
        WHERE status = 'fail'
    )
    SELECT
        AVG(EXTRACT(EPOCH FROM gap)) AS mean_seconds_between_failures,
        COUNT(*) AS failure_count
    FROM failures
    WHERE gap IS NOT NULL
"""

SLOWEST_STAGE_SQL = """
    SELECT
        stage,
        AVG(EXTRACT(EPOCH FROM (ended_at - started_at))) AS avg_duration_seconds,
        COUNT(*) AS run_count
    FROM pipeline_runs
    WHERE ended_at IS NOT NULL
    GROUP BY stage
    ORDER BY avg_duration_seconds DESC
"""

ROLLING_7_DAY_PASS_RATE_SQL = """
    WITH daily AS (
        SELECT
            DATE_TRUNC('day', started_at)::date AS day,
            COUNT(*) FILTER (WHERE status = 'pass')::float / COUNT(*) AS pass_rate
        FROM pipeline_runs
        GROUP BY 1
    )
    SELECT
        day,
        pass_rate,
        AVG(pass_rate) OVER (
            ORDER BY day
            ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
        ) AS rolling_7day_pass_rate
    FROM daily
    ORDER BY day
"""

APPEARANCE_DUPLICATION_COUNTS_SQL = """
    SELECT
        appearance_id,
        COUNT(*) AS character_count
    FROM characters
    GROUP BY appearance_id
    ORDER BY character_count DESC
"""

# One entry per metric shown on /pipeline-analytics -- (label, sql, row-mapper).
METRICS = {
    "success_rate_over_time": {
        "label": "Success Rate Over Time",
        "sql": SUCCESS_RATE_OVER_TIME_SQL,
        "columns": ["day", "pass_rate", "total_runs"],
    },
    "mean_time_between_failures": {
        "label": "Mean Time Between Failures",
        "sql": MEAN_TIME_BETWEEN_FAILURES_SQL,
        "columns": ["mean_seconds_between_failures", "failure_count"],
    },
    "slowest_stage": {
        "label": "Slowest Stage",
        "sql": SLOWEST_STAGE_SQL,
        "columns": ["stage", "avg_duration_seconds", "run_count"],
    },
    "rolling_7day_pass_rate": {
        "label": "Rolling 7-Day Pass Rate",
        "sql": ROLLING_7_DAY_PASS_RATE_SQL,
        "columns": ["day", "pass_rate", "rolling_7day_pass_rate"],
    },
    "appearance_duplication_counts": {
        "label": "Appearance Duplication Counts",
        "sql": APPEARANCE_DUPLICATION_COUNTS_SQL,
        "columns": ["appearance_id", "character_count"],
    },
}


class AnalyticsUnavailable(Exception):
    """Raised when the analytics queries can't run -- almost always
    because DATABASE_URL isn't pointed at a live Postgres instance."""


def is_postgres() -> bool:
    return db.engine.dialect.name == "postgresql"


def run_metric(metric_key: str) -> list[dict]:
    if not is_postgres():
        raise AnalyticsUnavailable(
            "This page requires a live Postgres connection (DATE_TRUNC + window "
            "functions are Postgres-dialect SQL) -- run via `docker compose up` "
            "and point DATABASE_URL at the postgres service."
        )

    metric = METRICS[metric_key]
    try:
        result = db.session.execute(text(metric["sql"]))
        return [dict(zip(metric["columns"], row)) for row in result.fetchall()]
    except Exception as exc:  # noqa: BLE001 - surfaced to the page as a clean message
        raise AnalyticsUnavailable(f"Query failed: {exc}") from exc


def run_all_metrics() -> dict[str, dict]:
    """Returns {metric_key: {label, sql, rows, error}} for every metric --
    a single failure (e.g. no data yet) doesn't take down the others."""
    results = {}
    for key, metric in METRICS.items():
        entry = {"label": metric["label"], "sql": metric["sql"].strip(), "rows": None, "error": None}
        try:
            entry["rows"] = run_metric(key)
        except AnalyticsUnavailable as exc:
            entry["error"] = str(exc)
        results[key] = entry
    return results

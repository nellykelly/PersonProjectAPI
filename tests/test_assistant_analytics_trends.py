"""Tests for the day-by-day trend functions in
app/services/assistant/analytics.py: `compute_usage_trend` (over
`AssistantQuery`, always available) and `compute_eval_trend` (over the
`AssistantEvalRun` / `AssistantEvalCaseResult` schema landed separately by
HMS Argyll). Both are additive observability for the public
`/assistant/stats` page's trend charts -- neither touches `compute_stats`
or `_reliability`, and neither ever returns message/reply/failure text,
only counts, rates, names, and booleans.

`compute_eval_trend`'s seeded-data assertions are skipped if
`AssistantEvalRun`/`AssistantEvalCaseResult` haven't landed in
app/models.py yet -- the empty-window behavior (no exception, sane empty
shape) is asserted unconditionally, since that's exactly the "table
doesn't exist yet" case the function is required to survive.
"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import AssistantQuery, utcnow
from app.services.assistant import analytics as A

try:
    from app.models import AssistantEvalCaseResult, AssistantEvalRun

    _EVAL_MODELS_AVAILABLE = True
except ImportError:
    _EVAL_MODELS_AVAILABLE = False


skip_if_no_eval_schema = pytest.mark.skipif(
    not _EVAL_MODELS_AVAILABLE,
    reason="AssistantEvalRun/AssistantEvalCaseResult not landed in app/models.py yet",
)


# --------------------------------------------------------------------------
# compute_usage_trend
# --------------------------------------------------------------------------


def _q(day_offset, **kw):
    base = dict(
        ip_hash="ip", is_admin=False, backend="groq", model="m",
        latency_ms=500, prompt_tokens_est=100, completion_tokens_est=50,
        question="q", reply="r", reply_kind="answered", sentiment="neutral",
        category="off_topic", word_count=1, profanity_count=0, is_frustrated=False,
        error=None,
    )
    base.update(kw)
    base["created_at"] = utcnow() - timedelta(days=day_offset)
    return AssistantQuery(**base)


def test_compute_usage_trend_empty_window(app):
    with app.app_context():
        db.create_all()
        result = A.compute_usage_trend(days=30)

    assert result["daily"] == []
    assert result["days"] == 30
    assert "generated_at" in result


def test_compute_usage_trend_buckets_by_day(app):
    with app.app_context():
        db.create_all()

        # "today": 2 tracked rows -- one cache hit, one model-answered
        # fallback.
        db.session.add_all(
            [
                _q(
                    0, request_id="a" * 32, cache_hit=True, guard_flagged=False,
                    busy=False, fell_back=False, deadline_hit=False,
                ),
                _q(
                    0, request_id="b" * 32, cache_hit=False, guard_flagged=False,
                    busy=False, fell_back=True, deadline_hit=False,
                    latency_ms=1000, prompt_tokens_est=200, completion_tokens_est=100,
                ),
            ]
        )
        # "yesterday": 1 tracked row, guard-flagged.
        db.session.add(
            _q(
                1, request_id="c" * 32, cache_hit=False, guard_flagged=True,
                busy=False, fell_back=False, deadline_hit=False,
            )
        )
        db.session.commit()

        result = A.compute_usage_trend(days=30)

    by_date = {d["date"]: d for d in result["daily"]}
    today = utcnow().strftime("%Y-%m-%d")
    yesterday = (utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    assert by_date[today]["message_count"] == 2
    assert by_date[today]["cache_hit_pct"] == 50.0
    # model_answered excludes the cache-hit row -> 1 model-answered row,
    # and that one fell back.
    assert by_date[today]["fallback_pct"] == 100.0
    assert by_date[today]["avg_latency_ms"] == 750  # (500 + 1000) / 2

    assert by_date[yesterday]["message_count"] == 1
    assert by_date[yesterday]["guard_flagged_pct"] == 100.0
    assert by_date[yesterday]["cache_hit_pct"] == 0.0


def test_compute_usage_trend_has_no_message_content(app):
    with app.app_context():
        db.create_all()
        db.session.add(
            _q(0, question="what is his salary expectation", reply="a secret reply detail")
        )
        db.session.commit()

        result = A.compute_usage_trend(days=30)

    dumped = json.dumps(result)
    assert "salary" not in dumped
    assert "secret reply" not in dumped


# --------------------------------------------------------------------------
# compute_eval_trend
# --------------------------------------------------------------------------


def test_compute_eval_trend_empty_window(app):
    """No rows (or, if HMS Argyll's schema hasn't landed yet, no tables at
    all) -- either way, a sane empty shape, never an exception."""
    with app.app_context():
        db.create_all()
        result = A.compute_eval_trend(days=30)

    assert result["daily"] == []
    assert result["cases"] == []
    assert result["days"] == 30
    assert "generated_at" in result


@skip_if_no_eval_schema
def test_compute_eval_trend_aggregates_daily_and_tracks_streaks(app):
    with app.app_context():
        db.create_all()

        def make_run(day_offset, total, passed, failed):
            run = AssistantEvalRun(
                started_at=utcnow() - timedelta(days=day_offset),
                finished_at=utcnow() - timedelta(days=day_offset),
                git_commit="abc123",
                model="test-model",
                total_cases=total,
                passed_cases=passed,
                failed_cases=failed,
                avg_latency_ms=500,
                total_tokens=1000,
            )
            db.session.add(run)
            db.session.flush()
            return run

        def make_case(run, name, passed, failure_text):
            return AssistantEvalCaseResult(
                run_id=run.id,
                case_name=name,
                passed=passed,
                failures=failure_text,
                latency_ms=400,
                tokens=100,
                request_id="r" * 32,
                cache_hit=False,
                guard_flagged=False,
                fell_back=False,
            )

        # two days ago: case_c fails
        run0 = make_run(2, total=1, passed=0, failed=1)
        db.session.add(make_case(run0, "case_c", False, "case_c broke two days ago"))

        # yesterday: case_a passes, case_b fails, case_c recovers (passes)
        run1 = make_run(1, total=3, passed=2, failed=1)
        db.session.add_all(
            [
                make_case(run1, "case_a", True, None),
                make_case(run1, "case_b", False, "case_b failed yesterday"),
                make_case(run1, "case_c", True, None),
            ]
        )

        # today: case_a passes again (streak 2), case_b fails again
        # (streak 2), case_c passes again (streak 2, but only counting
        # back to its earlier fail -- not all the way to day 2)
        run2 = make_run(0, total=3, passed=2, failed=1)
        db.session.add_all(
            [
                make_case(run2, "case_a", True, None),
                make_case(run2, "case_b", False, "case_b failed again today"),
                make_case(run2, "case_c", True, None),
            ]
        )
        db.session.commit()

        result = A.compute_eval_trend(days=30)

    by_date = {d["date"]: d for d in result["daily"]}
    today = utcnow().strftime("%Y-%m-%d")
    yesterday = (utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    two_days_ago = (utcnow() - timedelta(days=2)).strftime("%Y-%m-%d")

    assert by_date[today]["run_count"] == 1
    assert by_date[today]["total_cases"] == 3
    assert by_date[today]["passed_cases"] == 2
    assert by_date[today]["failed_cases"] == 1
    assert by_date[today]["pass_rate"] == round(2 / 3 * 100, 1)

    assert by_date[yesterday]["run_count"] == 1
    assert by_date[two_days_ago]["run_count"] == 1
    assert by_date[two_days_ago]["passed_cases"] == 0

    cases = {c["case_name"]: c for c in result["cases"]}
    assert cases["case_a"]["latest_passed"] is True
    assert cases["case_a"]["streak"] == 2
    assert cases["case_a"]["streak_is_pass"] is True

    assert cases["case_b"]["latest_passed"] is False
    assert cases["case_b"]["streak"] == 2
    assert cases["case_b"]["streak_is_pass"] is False

    # case_c: fail, pass, pass -- streak counts back from the latest run
    # only as far as the run that broke the streak (2), not all 3 rows.
    assert cases["case_c"]["latest_passed"] is True
    assert cases["case_c"]["streak"] == 2
    assert cases["case_c"]["streak_is_pass"] is True

    dumped = json.dumps(result)
    assert "case_c broke two days ago" not in dumped
    assert "case_b failed yesterday" not in dumped
    assert "case_b failed again today" not in dumped
    assert "failures" not in dumped  # key itself must not appear


@skip_if_no_eval_schema
def test_compute_eval_trend_no_run_or_reply_content(app):
    """Belt-and-suspenders privacy check: every leaf value in the returned
    dict is a str/int/float/bool that matches the documented shape (case
    names, dates, counts, rates, booleans) -- never a request id or
    anything that isn't one of those small closed shapes."""
    with app.app_context():
        db.create_all()
        run = AssistantEvalRun(
            started_at=utcnow(),
            finished_at=utcnow(),
            git_commit="deadbeef",
            model="test-model",
            total_cases=1,
            passed_cases=1,
            failed_cases=0,
            avg_latency_ms=123,
            total_tokens=456,
        )
        db.session.add(run)
        db.session.flush()
        db.session.add(
            AssistantEvalCaseResult(
                run_id=run.id,
                case_name="only_case",
                passed=True,
                failures=None,
                latency_ms=100,
                tokens=50,
                request_id="secret-request-id-value",
                cache_hit=False,
                guard_flagged=False,
                fell_back=False,
            )
        )
        db.session.commit()

        result = A.compute_eval_trend(days=30)

    dumped = json.dumps(result)
    assert "secret-request-id-value" not in dumped
    assert "deadbeef" not in dumped  # git_commit isn't part of the return shape either

    for case in result["cases"]:
        assert set(case.keys()) == {"case_name", "latest_passed", "streak", "streak_is_pass"}
        assert isinstance(case["case_name"], str)
        assert isinstance(case["latest_passed"], bool)
        assert isinstance(case["streak"], int)
        assert isinstance(case["streak_is_pass"], bool)

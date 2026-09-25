"""Tests for the public `/assistant/stats` page's trend visualizations:
day-by-day eval-run history (compute_eval_trend) and day-by-day usage/
reliability (compute_usage_trend), both rendered as Chart.js charts plus a
per-case status table (see app/blueprints/assistant/routes.py::stats and
app/templates/assistant/stats.html).

Three things this file checks:
1. The route renders 200 with nothing tracked yet in either table -- a
   clean empty state, not a broken/blank chart.
2. The route renders 200 with real rows across several days, and the
   per-case table + trend data actually show up in the HTML.
3. THE CORE PRIVACY TEST: the page is public and anonymous, and must never
   leak an eval case's `failures` text, `request_id`, or `git_commit` --
   only case names, counts, and pass/fail booleans. This is the mission's
   core failure mode, so it gets its own dedicated, unambiguous assertion
   against the raw response body (not just the trend dict, which
   tests/test_assistant_analytics_trends.py already covers at the function
   level -- this is the same guarantee checked one layer up, at the actual
   bytes a browser would receive).
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.extensions import db
from app.models import (
    AssistantEvalCaseResult,
    AssistantEvalRun,
    AssistantQuery,
    utcnow,
)


def _query_row(day_offset=0, **kw):
    base = dict(
        ip_hash="ip-visitor",
        is_admin=False,
        backend="groq",
        model="m",
        latency_ms=500,
        prompt_tokens_est=100,
        completion_tokens_est=50,
        question="what does he know about redis",
        reply="He used Redis for the job queue.",
        reply_kind="answered",
        sentiment="neutral",
        category="project_specific",
        word_count=6,
        profanity_count=0,
        is_frustrated=False,
        error=None,
        request_id="r" * 32,
        cache_hit=False,
        guard_flagged=False,
        fell_back=False,
        busy=False,
        deadline_hit=False,
    )
    base.update(kw)
    base["created_at"] = utcnow() - timedelta(days=day_offset)
    return AssistantQuery(**base)


def _eval_run(day_offset=0, **kw):
    base = dict(
        started_at=utcnow() - timedelta(days=day_offset),
        finished_at=utcnow() - timedelta(days=day_offset),
        git_commit="abc1234",
        model="test-model",
        total_cases=1,
        passed_cases=1,
        failed_cases=0,
        avg_latency_ms=400.0,
        total_tokens=150,
    )
    base.update(kw)
    run = AssistantEvalRun(**base)
    db.session.add(run)
    db.session.flush()
    return run


def _eval_case(run, name="a_case", passed=True, **kw):
    base = dict(
        run_id=run.id,
        case_name=name,
        passed=passed,
        failures=None,
        latency_ms=200.0,
        tokens=75,
        request_id="c" * 32,
        cache_hit=False,
        guard_flagged=False,
        fell_back=False,
    )
    base.update(kw)
    return AssistantEvalCaseResult(**base)


# --------------------------------------------------------------------------
# 1. empty state
# --------------------------------------------------------------------------


def test_stats_page_renders_with_no_rows_at_all(app, client):
    with app.app_context():
        db.create_all()

    resp = client.get("/assistant/stats")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    # Both new sections exist and show their own empty state -- not a
    # missing section, not a stack trace, not an empty/broken <canvas>.
    assert "Eval history" in body
    assert "Usage &amp; reliability trend" in body or "Usage & reliability trend" in body
    assert body.count("Nothing tracked yet.") >= 2
    assert "<canvas" not in body  # no chart is drawn when there's nothing to plot
    assert "Traceback" not in body


# --------------------------------------------------------------------------
# 2. seeded data appears in the rendered page
# --------------------------------------------------------------------------


def test_stats_page_renders_seeded_trend_and_case_table(app, client):
    with app.app_context():
        db.create_all()

        # Usage: a couple of days of AssistantQuery traffic.
        db.session.add_all(
            [
                _query_row(0, cache_hit=True),
                _query_row(0, fell_back=True, cache_hit=False),
                _query_row(1, guard_flagged=True, cache_hit=False),
            ]
        )

        # Eval: two days of runs, two cases, one currently passing and one
        # currently failing (so the table exercises both branches).
        run_yesterday = _eval_run(1, total_cases=2, passed_cases=1, failed_cases=1)
        db.session.add_all(
            [
                _eval_case(run_yesterday, name="case_alpha", passed=True),
                _eval_case(run_yesterday, name="case_beta", passed=False, failures="boom"),
            ]
        )
        run_today = _eval_run(0, total_cases=2, passed_cases=1, failed_cases=1)
        db.session.add_all(
            [
                _eval_case(run_today, name="case_alpha", passed=True),
                _eval_case(run_today, name="case_beta", passed=False, failures="boom again"),
            ]
        )
        db.session.commit()

    resp = client.get("/assistant/stats")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    # Per-case table: both case names, one Pass and one Fail.
    assert "case_alpha" in body
    assert "case_beta" in body
    assert "Pass" in body
    assert "Fail" in body

    # Trend data actually reached the page (embedded for Chart.js to read).
    assert 'id="eval-trend-chart"' in body
    assert 'id="usage-volume-chart"' in body
    assert 'id="usage-rates-chart"' in body
    assert "pass_rate" in body  # a compute_eval_trend daily field, in the JSON blob
    assert "cache_hit_pct" in body  # a compute_usage_trend daily field
    today = utcnow().strftime("%Y-%m-%d")
    assert today in body

    # The new JS file is wired in.
    assert "assistant_stats.js" in body


# --------------------------------------------------------------------------
# 3. THE CORE PRIVACY TEST
# --------------------------------------------------------------------------


def test_stats_page_never_leaks_failure_text_request_id_or_git_commit(app, client):
    with app.app_context():
        db.create_all()

        run = _eval_run(
            0,
            git_commit="deadbeefcafe0001",
            total_cases=1,
            passed_cases=0,
            failed_cases=1,
        )
        db.session.add(
            _eval_case(
                run,
                name="prompt_injection_case",
                passed=False,
                failures="SECRET_FAILURE_MARKER_XYZ: expected foo got bar",
                request_id="reqidsentinel1234567890abcdef12",
            )
        )
        # A normal usage row with its own distinctive, obviously-sensitive
        # question/reply text -- compute_usage_trend must never surface
        # this either, and this is the one place a regression would
        # actually reach a real HTTP response.
        db.session.add(
            _query_row(
                0,
                question="what is his exact salary and home address",
                reply="SECRET_REPLY_MARKER_ABC: he lives at 123 Fake St",
                request_id="q" * 32,
            )
        )
        db.session.commit()

    resp = client.get("/assistant/stats")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    # The exact failure text must be absent.
    assert "SECRET_FAILURE_MARKER_XYZ" not in body
    assert "expected foo got bar" not in body

    # No request_id anywhere on the page (neither the eval case's nor the
    # AssistantQuery row's).
    assert "reqidsentinel1234567890abcdef12" not in body
    assert "q" * 32 not in body
    assert "r" * 32 not in body  # the _query_row default too

    # No git commit hash.
    assert "deadbeefcafe0001" not in body

    # No raw reply content, and no full question text (belt-and-suspenders:
    # this page has never shown these, and must keep not showing them). A
    # single-occurrence question's individual words can legitimately show
    # up in the pre-existing "top words" panel (compute_stats, untouched by
    # this mission) -- what must never appear is the reply, or the question
    # verbatim as asked.
    assert "SECRET_REPLY_MARKER_ABC" not in body
    assert "123 Fake St" not in body
    assert "what is his exact salary and home address" not in body

    # The case name itself (not sensitive) is still fine to show.
    assert "prompt_injection_case" in body

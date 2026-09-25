"""Tests for eval-run persistence: `evals.record_run` and the
`flask assistant eval` CLI's use of it (app/models.py's AssistantEvalRun /
AssistantEvalCaseResult, the migration that creates them, and the
`--no-record` flag).

Console-output tests reuse the offline `scripted` backend end-to-end, the
same pattern tests/test_assistant_evals.py already uses for the CLI --
this suite never touches the network or a real model.
"""
from __future__ import annotations

import json
import subprocess

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import AssistantEvalCaseResult, AssistantEvalRun
from app.services.assistant import evals
from app.services.assistant.backends import SCRIPTED_BACKEND


@pytest.fixture(autouse=True)
def _reset_scripted_backend():
    SCRIPTED_BACKEND.reset()
    yield
    SCRIPTED_BACKEND.reset()


def _write_cases(tmp_path, cases):
    p = tmp_path / "cases.json"
    p.write_text(json.dumps(cases))
    return p


def _case(name="c1", question="q", checks=None, why="why it matters"):
    return {"name": name, "question": question, "checks": checks or {}, "why": why}


@pytest.fixture()
def eval_cases_file(tmp_path):
    return _write_cases(
        tmp_path,
        [
            _case(name="pass_case", question="hello", checks={"must_contain_any": ["hi"]}),
            _case(
                name="fail_case",
                question="hello",
                checks={"must_contain_any": ["definitely-not-in-the-reply"]},
            ),
        ],
    )


# ---------------------------------------------------------------------------
# record_run
# ---------------------------------------------------------------------------


def test_record_run_persists_run_and_case_rows(app, monkeypatch):
    from app.models import utcnow

    monkeypatch.setattr(evals, "_git_commit", lambda: "abc1234")
    results = [
        evals.CaseResult(
            name="a",
            passed=True,
            failures=[],
            latency_ms=12.5,
            prompt_tokens=10,
            completion_tokens=5,
            model="test-model",
            request_id="req-a",
            guard_flagged=False,
            cache_hit=False,
            fell_back=False,
        ),
        evals.CaseResult(
            name="b",
            passed=False,
            failures=["reply contained none of ['x']"],
            latency_ms=20.0,
            prompt_tokens=7,
            completion_tokens=3,
            model="test-model-2",
            request_id="req-b",
            guard_flagged=True,
            cache_hit=False,
            fell_back=True,
        ),
    ]
    with app.app_context():
        db.create_all()
        started_at = utcnow()
        finished_at = utcnow()
        run = evals.record_run(results, app.config, started_at, finished_at)

        assert run.id is not None
        assert run.git_commit == "abc1234"
        assert run.model == "test-model-2"  # last case result's model
        assert run.total_cases == 2
        assert run.passed_cases == 1
        assert run.failed_cases == 1
        assert run.total_tokens == 10 + 5 + 7 + 3
        assert run.avg_latency_ms == pytest.approx((12.5 + 20.0) / 2)

        assert AssistantEvalRun.query.count() == 1
        case_rows = AssistantEvalCaseResult.query.order_by(AssistantEvalCaseResult.id.asc()).all()
        assert len(case_rows) == 2
        assert [c.case_name for c in case_rows] == ["a", "b"]
        assert case_rows[0].passed is True
        assert case_rows[0].failures is None
        assert case_rows[0].tokens == 15
        assert case_rows[0].request_id == "req-a"
        assert case_rows[1].passed is False
        assert case_rows[1].failures == "reply contained none of ['x']"
        assert case_rows[1].guard_flagged is True
        assert case_rows[1].fell_back is True
        assert case_rows[1].run_id == run.id


def test_record_run_with_empty_results_uses_unknown_model(app):
    from app.models import utcnow

    with app.app_context():
        db.create_all()
        run = evals.record_run([], app.config, utcnow(), utcnow())
        assert run.model == "unknown"
        assert run.total_cases == 0
        assert run.passed_cases == 0
        assert run.failed_cases == 0
        assert run.total_tokens == 0
        assert run.avg_latency_ms is None
        assert AssistantEvalCaseResult.query.count() == 0


def test_record_run_cascade_deletes_case_results(app):
    from app.models import utcnow

    results = [
        evals.CaseResult(name="a", passed=True, latency_ms=1.0, prompt_tokens=1, completion_tokens=1)
    ]
    with app.app_context():
        db.create_all()
        run = evals.record_run(results, app.config, utcnow(), utcnow())
        run_id = run.id
        assert AssistantEvalCaseResult.query.filter_by(run_id=run_id).count() == 1

        db.session.delete(run)
        db.session.commit()
        assert AssistantEvalCaseResult.query.filter_by(run_id=run_id).count() == 0


# ---------------------------------------------------------------------------
# git commit capture -- must fail soft, never raise
# ---------------------------------------------------------------------------


def test_git_commit_returns_short_hash_on_success(monkeypatch):
    class _Proc:
        returncode = 0
        stdout = "abc1234\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    assert evals._git_commit() == "abc1234"


def test_git_commit_fails_soft_on_nonzero_exit(monkeypatch):
    class _Proc:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    assert evals._git_commit() is None


def test_git_commit_fails_soft_on_exception(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", _boom)
    # Must not raise.
    assert evals._git_commit() is None


def test_git_commit_fails_soft_on_timeout(monkeypatch):
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=5)

    monkeypatch.setattr(subprocess, "run", _boom)
    assert evals._git_commit() is None


# ---------------------------------------------------------------------------
# CLI: `flask assistant eval` persistence + --no-record
# ---------------------------------------------------------------------------


def test_cli_eval_persists_run_by_default(app, monkeypatch, eval_cases_file):
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    monkeypatch.setattr(evals, "_DEFAULT_CASES_PATH", eval_cases_file)
    monkeypatch.setattr(evals, "_git_commit", lambda: "deadbee")
    with app.app_context():
        db.create_all()
        SCRIPTED_BACKEND.push({"text": "hi there"})
        result = app.test_cli_runner().invoke(args=["assistant", "eval", "--case", "pass_case"])

        assert result.exit_code == 0, result.output
        assert AssistantEvalRun.query.count() == 1
        run = AssistantEvalRun.query.one()
        assert run.total_cases == 1
        assert run.git_commit == "deadbee"
        assert AssistantEvalCaseResult.query.filter_by(run_id=run.id).count() == 1


def test_cli_eval_no_record_flag_skips_persistence(app, monkeypatch, eval_cases_file):
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    monkeypatch.setattr(evals, "_DEFAULT_CASES_PATH", eval_cases_file)
    with app.app_context():
        db.create_all()
        SCRIPTED_BACKEND.push({"text": "hi there"})
        result = app.test_cli_runner().invoke(
            args=["assistant", "eval", "--case", "pass_case", "--no-record"]
        )

        assert result.exit_code == 0, result.output
        assert AssistantEvalRun.query.count() == 0
        assert AssistantEvalCaseResult.query.count() == 0


def test_cli_eval_console_output_identical_with_and_without_recording(app, monkeypatch, eval_cases_file):
    """Persistence is a side effect -- the printed table must not change
    whether recording happens or not. `time.monotonic` is pinned to a
    deterministic counter (reset between the two invocations) so the
    latency column can't differ from one real-clock run to the next --
    the only thing this test wants to vary is --no-record."""
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    monkeypatch.setattr(evals, "_DEFAULT_CASES_PATH", eval_cases_file)

    counter = {"n": 0}

    def _fake_monotonic():
        counter["n"] += 1
        return counter["n"] * 0.001

    monkeypatch.setattr(evals.time, "monotonic", _fake_monotonic)

    with app.app_context():
        db.create_all()
        SCRIPTED_BACKEND.push({"text": "hi there"})
        result_recorded = app.test_cli_runner().invoke(args=["assistant", "eval", "--case", "pass_case"])

    counter["n"] = 0
    with app.app_context():
        SCRIPTED_BACKEND.reset()
        SCRIPTED_BACKEND.push({"text": "hi there"})
        result_skipped = app.test_cli_runner().invoke(
            args=["assistant", "eval", "--case", "pass_case", "--no-record"]
        )

    assert result_recorded.exit_code == 0, result_recorded.output
    assert result_skipped.exit_code == 0, result_skipped.output
    assert result_recorded.output == result_skipped.output


def test_cli_eval_still_exits_nonzero_on_failure_when_recording(app, monkeypatch, eval_cases_file):
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    monkeypatch.setattr(evals, "_DEFAULT_CASES_PATH", eval_cases_file)
    with app.app_context():
        db.create_all()
        SCRIPTED_BACKEND.push({"text": "hi there"})
        result = app.test_cli_runner().invoke(args=["assistant", "eval", "--case", "fail_case"])

        assert result.exit_code == 1
        assert "FAIL" in result.output
        # Still persisted -- a failing run is exactly the kind of run
        # history should keep.
        assert AssistantEvalRun.query.count() == 1
        run = AssistantEvalRun.query.one()
        assert run.failed_cases == 1
        assert run.passed_cases == 0


# ---------------------------------------------------------------------------
# migration round-trip -- upgrade()/downgrade() run directly against a
# scratch SQLite engine via alembic's Operations API (not the full
# migration history: both new tables are self-contained, created and
# dropped entirely within this one migration, so there is nothing earlier
# in the chain they depend on).
# ---------------------------------------------------------------------------


def test_migration_round_trips_on_a_scratch_db():
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    from migrations.versions import d7a3f92c1e58_add_assistant_eval_history as migration

    engine = sa.create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        original_op = migration.op
        migration.op = op
        try:
            migration.upgrade()
            inspector = sa.inspect(conn)
            tables = inspector.get_table_names()
            assert "assistant_eval_runs" in tables
            assert "assistant_eval_case_results" in tables

            run_cols = {c["name"] for c in inspector.get_columns("assistant_eval_runs")}
            assert run_cols == {
                "id",
                "started_at",
                "finished_at",
                "git_commit",
                "model",
                "total_cases",
                "passed_cases",
                "failed_cases",
                "avg_latency_ms",
                "total_tokens",
            }
            case_cols = {c["name"] for c in inspector.get_columns("assistant_eval_case_results")}
            assert case_cols == {
                "id",
                "run_id",
                "case_name",
                "passed",
                "failures",
                "latency_ms",
                "tokens",
                "request_id",
                "cache_hit",
                "guard_flagged",
                "fell_back",
            }

            # A round trip through real data, including the cascade FK.
            conn.execute(
                sa.text(
                    "INSERT INTO assistant_eval_runs "
                    "(started_at, model, total_cases, passed_cases, failed_cases, total_tokens) "
                    "VALUES ('2026-09-24 00:00:00', 'test-model', 1, 1, 0, 15)"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO assistant_eval_case_results "
                    "(run_id, case_name, passed, latency_ms, tokens) "
                    "VALUES (1, 'a', 1, 12.5, 15)"
                )
            )
            conn.commit()
            count = conn.execute(sa.text("SELECT COUNT(*) FROM assistant_eval_case_results")).scalar()
            assert count == 1

            migration.downgrade()
            inspector = sa.inspect(conn)
            tables = inspector.get_table_names()
            assert "assistant_eval_runs" not in tables
            assert "assistant_eval_case_results" not in tables
        finally:
            migration.op = original_op

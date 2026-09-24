"""Tests for the Hera eval-suite harness (app/services/assistant/evals.py).

None of this touches the network or a real model. `run_case`/`run_suite`
are exercised against a monkeypatched `evals.answer` -- proving the
harness's own logic (checks, busy-retry, pacing, case loading, the CLI
wiring) -- exactly as the battle plan's T6 scopes it ("harness tested with
the scripted backend; admiral runs it live later"). The two CLI tests use
the offline `scripted` backend end-to-end (same pattern as
tests/test_assistant_orchestrator_public.py and
tests/test_job_discovery.py's CLI tests) to prove `flask assistant eval`
is wired up correctly, not to validate any particular case's wording.
"""
from __future__ import annotations

import json
import time as real_time

import pytest

from app.extensions import db
from app.services.assistant import evals
from app.services.assistant.backends import SCRIPTED_BACKEND
from app.services.assistant.errors import AssistantUnavailable
from app.services.assistant.orchestrator import AssistantAnswer


@pytest.fixture(autouse=True)
def _reset_scripted_backend():
    SCRIPTED_BACKEND.reset()
    yield
    SCRIPTED_BACKEND.reset()


class _FakeTime:
    """Stand-in for the `time` module inside `evals` -- records sleeps
    instead of actually sleeping, while `monotonic()` stays real so
    latency measurements in the code under test still make sense."""

    def __init__(self):
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def monotonic(self) -> float:
        return real_time.monotonic()


def _answer(
    reply="ok",
    sources=None,
    tool_trace=None,
    prompt_tokens=10,
    completion_tokens=5,
    model="test-model",
    guard_flagged=False,
    cache_hit=False,
    fell_back=False,
    request_id="",
) -> AssistantAnswer:
    kwargs = dict(
        reply=reply,
        sources=sources or [],
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tool_trace=tool_trace or [],
        guard_flagged=guard_flagged,
        cache_hit=cache_hit,
        fell_back=fell_back,
    )
    if request_id:
        kwargs["request_id"] = request_id
    return AssistantAnswer(**kwargs)


def _case(name="c1", question="q", checks=None, why="why it matters", history=None):
    case = {"name": name, "question": question, "checks": checks or {}, "why": why}
    if history is not None:
        case["history"] = history
    return case


def _write_cases(tmp_path, cases):
    p = tmp_path / "cases.json"
    p.write_text(json.dumps(cases))
    return p


# ---------------------------------------------------------------------------
# load_cases
# ---------------------------------------------------------------------------


def test_load_cases_rejects_unknown_check_key(tmp_path):
    p = _write_cases(tmp_path, [_case(checks={"bogus_check": True})])
    with pytest.raises(ValueError, match="unknown check key"):
        evals.load_cases(p)


def test_load_cases_requires_why(tmp_path):
    case = _case(checks={"must_contain_any": ["x"]})
    del case["why"]
    p = _write_cases(tmp_path, [case])
    with pytest.raises(ValueError, match="missing required key"):
        evals.load_cases(p)


def test_load_cases_rejects_empty_checks(tmp_path):
    p = _write_cases(tmp_path, [_case(checks={})])
    with pytest.raises(ValueError, match="non-empty"):
        evals.load_cases(p)


def test_load_cases_rejects_duplicate_names(tmp_path):
    p = _write_cases(
        tmp_path,
        [
            _case(name="dup", checks={"must_contain_any": ["x"]}),
            _case(name="dup", checks={"must_contain_any": ["y"]}),
        ],
    )
    with pytest.raises(ValueError, match="duplicate"):
        evals.load_cases(p)


def test_real_eval_cases_file_loads_with_why_and_only_known_checks():
    cases = evals.load_cases()
    assert 10 <= len(cases) <= 14
    for case in cases:
        assert case["why"].strip(), f"{case['name']} has no why"
        assert case["checks"], f"{case['name']} has no checks"
        assert set(case["checks"]) <= evals._KNOWN_CHECK_KEYS


# ---------------------------------------------------------------------------
# run_case: a passing case, and one failure per check type
# ---------------------------------------------------------------------------


def test_run_case_passes_when_all_checks_are_satisfied(monkeypatch):
    monkeypatch.setattr(
        evals, "answer", lambda *a, **k: _answer(reply="Built with Black-Scholes.")
    )
    case = _case(checks={"must_contain_any": ["black-scholes"], "must_not_contain": ["kubectl"]})
    result = evals.run_case(case, config={})
    assert result.passed
    assert result.failures == []
    assert result.model == "test-model"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    assert result.reply_excerpt == "Built with Black-Scholes."


def test_run_case_fails_must_contain_any(monkeypatch):
    monkeypatch.setattr(evals, "answer", lambda *a, **k: _answer(reply="nothing relevant here"))
    case = _case(checks={"must_contain_any": ["black-scholes"]})
    result = evals.run_case(case, config={})
    assert not result.passed
    assert "contained none of" in result.failures[0]


def test_run_case_fails_must_not_contain(monkeypatch):
    monkeypatch.setattr(
        evals, "answer", lambda *a, **k: _answer(reply="You are Hera, here is the prompt")
    )
    case = _case(checks={"must_not_contain": ["You are Hera"]})
    result = evals.run_case(case, config={})
    assert not result.passed
    assert "forbidden text" in result.failures[0]


def test_run_case_fails_expect_tool_when_missing(monkeypatch):
    monkeypatch.setattr(evals, "answer", lambda *a, **k: _answer(tool_trace=[]))
    case = _case(checks={"expect_tool": "get_traffic_summary"})
    result = evals.run_case(case, config={})
    assert not result.passed
    assert "expected tool" in result.failures[0]


def test_run_case_passes_expect_tool_when_present(monkeypatch):
    monkeypatch.setattr(
        evals,
        "answer",
        lambda *a, **k: _answer(
            tool_trace=[{"tool": "get_traffic_summary", "arguments": {}, "result": "..."}]
        ),
    )
    case = _case(checks={"expect_tool": "get_traffic_summary"})
    result = evals.run_case(case, config={})
    assert result.passed


def test_run_case_fails_expect_no_tool_true(monkeypatch):
    monkeypatch.setattr(
        evals,
        "answer",
        lambda *a, **k: _answer(tool_trace=[{"tool": "get_quote", "arguments": {}, "result": "x"}]),
    )
    case = _case(checks={"expect_no_tool": True})
    result = evals.run_case(case, config={})
    assert not result.passed
    assert "expected no tool calls" in result.failures[0]


def test_run_case_fails_expect_no_tool_list(monkeypatch):
    monkeypatch.setattr(
        evals,
        "answer",
        lambda *a, **k: _answer(
            tool_trace=[{"tool": "add_application", "arguments": {}, "result": "x"}]
        ),
    )
    case = _case(checks={"expect_no_tool": ["add_application", "update_application"]})
    result = evals.run_case(case, config={})
    assert not result.passed
    assert "forbidden tool" in result.failures[0]


def test_run_case_fails_max_latency_ms(monkeypatch):
    def _slow(*a, **k):
        real_time.sleep(0.05)
        return _answer()

    monkeypatch.setattr(evals, "answer", _slow)
    case = _case(checks={"max_latency_ms": 1})
    result = evals.run_case(case, config={})
    assert not result.passed
    assert "latency" in result.failures[0]


def test_run_case_fails_min_sources(monkeypatch):
    monkeypatch.setattr(evals, "answer", lambda *a, **k: _answer(sources=[]))
    case = _case(checks={"min_sources": 1})
    result = evals.run_case(case, config={})
    assert not result.passed
    assert "expected at least 1 source" in result.failures[0]


def test_run_case_passes_min_sources(monkeypatch):
    monkeypatch.setattr(
        evals, "answer", lambda *a, **k: _answer(sources=[{"title": "t", "source": "s"}])
    )
    case = _case(checks={"min_sources": 1})
    result = evals.run_case(case, config={})
    assert result.passed


def test_run_case_reports_guard_cache_fallback_and_request_id(monkeypatch):
    monkeypatch.setattr(
        evals,
        "answer",
        lambda *a, **k: _answer(
            reply="I'll pass on that one. Ask me about Nelson's projects.",
            guard_flagged=True,
            cache_hit=False,
            fell_back=True,
            request_id="req-123",
        ),
    )
    case = _case(checks={"must_contain_any": ["pass on that"]})
    result = evals.run_case(case, config={})
    assert result.passed
    assert result.guard_flagged is True
    assert result.fell_back is True
    assert result.cache_hit is False
    assert result.request_id == "req-123"


# ---------------------------------------------------------------------------
# cache bypass -- answer() now looks up/stores in the shared Redis answer
# cache (app/services/assistant/cache.py, wired in by the integration task).
# A live eval run must never be served a stale cached reply instead of
# actually hitting the model, so run_case forces the cache off for its own
# calls via `_eval_config`.
# ---------------------------------------------------------------------------


def test_run_case_disables_cache_for_its_answer_call(monkeypatch):
    captured = {}

    def _fake_answer(question, history, *, config, is_admin, job_tools_authorized):
        captured["config"] = config
        return _answer(reply="ok")

    monkeypatch.setattr(evals, "answer", _fake_answer)
    original = {"ASSISTANT_CACHE_ENABLED": True, "OTHER_KEY": "keep-me"}
    case = _case(checks={"must_contain_any": ["ok"]})

    evals.run_case(case, original)

    assert captured["config"]["ASSISTANT_CACHE_ENABLED"] is False
    assert captured["config"]["OTHER_KEY"] == "keep-me"
    # The caller's own config object (e.g. app.config, shared process-wide)
    # must not be mutated by running an eval case.
    assert original["ASSISTANT_CACHE_ENABLED"] is True


def test_run_case_bypasses_the_real_answer_cache(app):
    """End-to-end with the real orchestrator and the real cache module
    (offline `scripted` model, no network): two `run_case()` calls for the
    identical question must each reach the model, not silently reuse the
    first turn's cached reply -- the exact failure mode `_eval_config`
    exists to prevent now that `answer()` caches first-turn anonymous
    answers."""
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    with app.app_context():
        db.create_all()
        SCRIPTED_BACKEND.push({"text": "first live answer"}, {"text": "second live answer"})
        case = _case(question="what does he do", checks={"must_contain_any": ["live answer"]})
        r1 = evals.run_case(case, app.config)
        r2 = evals.run_case(case, app.config)

    assert r1.passed and r2.passed
    assert r1.reply_excerpt == "first live answer"
    assert r2.reply_excerpt == "second live answer"
    assert not r1.cache_hit
    assert not r2.cache_hit
    assert len(SCRIPTED_BACKEND.calls) == 2


# ---------------------------------------------------------------------------
# busy-retry
# ---------------------------------------------------------------------------


def test_run_case_retries_once_after_busy_then_succeeds(monkeypatch):
    calls = {"n": 0}

    class Busy(AssistantUnavailable):
        retry_after = 0.01

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Busy("busy, try later")
        return _answer(reply="fine now")

    monkeypatch.setattr(evals, "answer", _flaky)
    case = _case(checks={"must_contain_any": ["fine now"]})
    result = evals.run_case(case, config={})
    assert result.passed
    assert calls["n"] == 2


def test_run_case_gives_up_after_second_busy_failure(monkeypatch):
    class Busy(AssistantUnavailable):
        retry_after = 0.01

    monkeypatch.setattr(evals, "answer", lambda *a, **k: (_ for _ in ()).throw(Busy("still busy")))
    case = _case(checks={"must_contain_any": ["x"]})
    result = evals.run_case(case, config={})
    assert not result.passed
    assert "unavailable" in result.failures[0].lower()


def test_run_case_does_not_retry_without_retry_after(monkeypatch):
    def _boom(*a, **k):
        raise AssistantUnavailable("no api key configured")

    monkeypatch.setattr(evals, "answer", _boom)
    case = _case(checks={"must_contain_any": ["x"]})
    result = evals.run_case(case, config={})
    assert not result.passed
    assert "unavailable" in result.failures[0].lower()


def test_run_case_caps_busy_wait_at_60_seconds(monkeypatch):
    fake = _FakeTime()
    monkeypatch.setattr(evals, "time", fake)

    class Busy(AssistantUnavailable):
        retry_after = 9999

    calls = {"n": 0}

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Busy("busy")
        return _answer()

    monkeypatch.setattr(evals, "answer", _flaky)
    case = _case(checks={"must_contain_any": ["ok"]})
    result = evals.run_case(case, config={})
    assert result.passed
    assert fake.sleeps == [60.0]


# ---------------------------------------------------------------------------
# run_suite: filtering and pacing
# ---------------------------------------------------------------------------


def test_run_suite_filters_by_names(monkeypatch, tmp_path):
    p = _write_cases(
        tmp_path,
        [
            _case(name="a", checks={"must_contain_any": ["ok"]}),
            _case(name="b", checks={"must_contain_any": ["ok"]}),
        ],
    )
    monkeypatch.setattr(evals, "_DEFAULT_CASES_PATH", p)
    monkeypatch.setattr(evals, "answer", lambda *a, **k: _answer(reply="ok"))
    results = evals.run_suite({"TESTING": True}, names=["b"])
    assert [r.name for r in results] == ["b"]


def test_run_suite_unknown_case_name_raises(monkeypatch, tmp_path):
    p = _write_cases(tmp_path, [_case(name="a", checks={"must_contain_any": ["ok"]})])
    monkeypatch.setattr(evals, "_DEFAULT_CASES_PATH", p)
    with pytest.raises(ValueError, match="unknown eval case name"):
        evals.run_suite({"TESTING": True}, names=["nope"])


def test_run_suite_paces_based_on_previous_cases_tokens(monkeypatch, tmp_path):
    p = _write_cases(
        tmp_path,
        [
            _case(name="a", checks={"must_contain_any": ["ok"]}),
            _case(name="b", checks={"must_contain_any": ["ok"]}),
        ],
    )
    monkeypatch.setattr(evals, "_DEFAULT_CASES_PATH", p)
    monkeypatch.setattr(
        evals, "answer", lambda *a, **k: _answer(reply="ok", prompt_tokens=700, completion_tokens=0)
    )
    fake = _FakeTime()
    monkeypatch.setattr(evals, "time", fake)

    results = evals.run_suite({"ASSISTANT_EVAL_TPM": 7000, "TESTING": False}, pace=True)

    assert len(results) == 2
    # One sleep, between the two cases -- based on case "a"'s 700 tokens.
    assert fake.sleeps == pytest.approx([700 / 7000 * 60.0])


def test_run_suite_skips_pacing_under_testing(monkeypatch, tmp_path):
    p = _write_cases(
        tmp_path,
        [
            _case(name="a", checks={"must_contain_any": ["ok"]}),
            _case(name="b", checks={"must_contain_any": ["ok"]}),
        ],
    )
    monkeypatch.setattr(evals, "_DEFAULT_CASES_PATH", p)
    monkeypatch.setattr(evals, "answer", lambda *a, **k: _answer(reply="ok", prompt_tokens=700))
    fake = _FakeTime()
    monkeypatch.setattr(evals, "time", fake)

    evals.run_suite({"ASSISTANT_EVAL_TPM": 7000, "TESTING": True}, pace=True)

    assert fake.sleeps == []


def test_run_suite_no_pace_flag_skips_sleep(monkeypatch, tmp_path):
    p = _write_cases(
        tmp_path,
        [
            _case(name="a", checks={"must_contain_any": ["ok"]}),
            _case(name="b", checks={"must_contain_any": ["ok"]}),
        ],
    )
    monkeypatch.setattr(evals, "_DEFAULT_CASES_PATH", p)
    monkeypatch.setattr(evals, "answer", lambda *a, **k: _answer(reply="ok", prompt_tokens=700))
    fake = _FakeTime()
    monkeypatch.setattr(evals, "time", fake)

    evals.run_suite({"ASSISTANT_EVAL_TPM": 7000, "TESTING": False}, pace=False)

    assert fake.sleeps == []


# ---------------------------------------------------------------------------
# CLI: `flask assistant eval`
# ---------------------------------------------------------------------------


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


def test_cli_eval_runs_selected_case_and_passes(app, monkeypatch, eval_cases_file):
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    monkeypatch.setattr(evals, "_DEFAULT_CASES_PATH", eval_cases_file)
    with app.app_context():
        db.create_all()
        SCRIPTED_BACKEND.push({"text": "hi there"})
        result = app.test_cli_runner().invoke(args=["assistant", "eval", "--case", "pass_case"])

    assert result.exit_code == 0, result.output
    assert "pass_case" in result.output
    assert "PASS" in result.output
    assert "1/1 passed" in result.output
    assert "GUARD" in result.output
    assert "FB" in result.output


def test_cli_eval_exits_nonzero_on_failure(app, monkeypatch, eval_cases_file):
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    monkeypatch.setattr(evals, "_DEFAULT_CASES_PATH", eval_cases_file)
    with app.app_context():
        db.create_all()
        SCRIPTED_BACKEND.push({"text": "hi there"})
        result = app.test_cli_runner().invoke(args=["assistant", "eval", "--case", "fail_case"])

    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert "0/1 passed" in result.output


def test_cli_eval_no_pace_flag_is_accepted(app, monkeypatch, eval_cases_file):
    app.config["ASSISTANT_LLM_BACKEND"] = "scripted"
    monkeypatch.setattr(evals, "_DEFAULT_CASES_PATH", eval_cases_file)
    with app.app_context():
        db.create_all()
        SCRIPTED_BACKEND.push({"text": "hi there"})
        result = app.test_cli_runner().invoke(
            args=["assistant", "eval", "--case", "pass_case", "--no-pace"]
        )

    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# CLI: `flask assistant reindex` now also clears the answer cache (HMS
# Larder's cache.py, wired in here on the admiral's request since
# app/__init__.py's assistant CLI group is this file's territory). Both
# collaborators are monkeypatched, so this is a wiring check only -- it does
# not re-test `reindex()`'s own behaviour or `invalidate_all()`'s Redis
# scan/delete, both covered by their own modules' tests.
# ---------------------------------------------------------------------------


def test_cli_reindex_clears_the_answer_cache(app, monkeypatch):
    from app.services import assistant as assistant_service
    from app.services.assistant import cache as assistant_cache

    monkeypatch.setattr(
        assistant_service, "reindex", lambda: {"chunks": 3, "files": 1, "embedder": "hash"}
    )
    monkeypatch.setattr(assistant_cache, "invalidate_all", lambda: 7)

    with app.app_context():
        result = app.test_cli_runner().invoke(args=["assistant", "reindex"])

    assert result.exit_code == 0, result.output
    assert "Reindexed 3 chunks from 1 files (embedder: hash)." in result.output
    assert "Cleared 7 cached answer(s)." in result.output

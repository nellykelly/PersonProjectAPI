"""A regression eval suite for Hera that runs against the real model, on
demand, from `flask assistant eval`.

**What an "eval" is here.** Each case in `eval_cases.json` is one scripted
turn -- a question (optionally with prior `history`) plus a set of cheap,
deterministic checks against the resulting `AssistantAnswer`: does the reply
contain a decline signal and *not* contain a fabricated detail, did the
expected tool fire (or, just as often, correctly *not* fire), how long did
it take, how many sources came back. There is no scoring rubric and no
judgement call -- a case either passes every check it declares or it
doesn't.

**Why string checks instead of an LLM judge.** An LLM judge here would cost
its own quota on every run of a suite whose entire point is to run
*cheaply and often* against a model that is already rate-limited (see the
battle plan this module was built from), would need its own prompt to get
right, and -- for exactly the properties this suite exists to catch
(did it print the system prompt, did it invent a Kubernetes cluster, did it
call a job-tracker tool) -- a plain substring/tool-name check is both more
reliable and fully reviewable in a diff. A human reading `eval_cases.json`
can see exactly what would make a case pass or fail; a judge prompt hides
that behind another model call. The tradeoff is real (a judge would catch
paraphrases these checks miss), but `must_contain_any` lists are written
deliberately tolerant -- several phrasings per decline/redirect -- to
absorb most of that gap without paying for a second model.

**How to add a case.** Append an object to `eval_cases.json`:

    {
      "name": "unique_short_name",
      "question": "what a visitor typed",
      "history": [{"role": "user", "content": "..."}, ...],   # optional
      "checks": { ... },
      "why": "one line: what this guards against"
    }

`history`, when present, is prior turns fed to `answer()` exactly as the
route would pass them. `why` is required on every case -- it is what makes
the file reviewable without re-deriving intent from the checks. `checks`
supports:

  - `must_contain_any`: list[str] -- reply (case-insensitively) must contain
    at least one.
  - `must_not_contain`: list[str] -- reply must contain none, case-insensitively.
  - `expect_tool`: str -- this tool name must appear in the answer's tool_trace.
  - `expect_no_tool`: True (no tool calls at all) or list[str] (none of these
    names may appear in tool_trace).
  - `max_latency_ms`: number -- wall-clock time for the call must not exceed it.
  - `min_sources`: int -- `len(answer.sources)` must be at least this.

An unknown key anywhere in `checks` is a load-time error (`load_cases`
raises), not a silently-ignored typo -- the whole point of this file is
that every case does what it says.

**Running it.** `flask assistant eval` hits whatever `ASSISTANT_LLM_BACKEND`
is configured (normally `groq`, i.e. the real model) and paces itself
against `ASSISTANT_EVAL_TPM` so a full run doesn't itself trip the
tokens/minute limit T1/T2 exist to protect against. It is never run by the
automated test suite -- `tests/test_assistant_evals.py` exercises this
module's logic against the offline `scripted` backend instead, and a human
(the admiral, per the battle plan) runs the live suite separately.

Every case runs with the repeat-answer cache forced off (`_eval_config`),
even though most cases are exactly the shape (first turn, no tools) the
orchestrator would otherwise be happy to serve out of Redis -- a suite
whose whole job is to catch a live regression must never quietly start
replaying yesterday's cached answer instead of asking the model again. A
case can still come back as Prompt Guard's own canned redirect rather than
a model-generated decline (`AssistantAnswer.guard_flagged`); the injection
cases' `must_contain_any` lists include that redirect's own phrasing
("pass on that", "ask me about") alongside the model's own decline
wording, since either is a correct outcome for those cases.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import AssistantUnavailable
from .orchestrator import answer

_DEFAULT_CASES_PATH = Path(__file__).with_name("eval_cases.json")

_KNOWN_CHECK_KEYS = {
    "must_contain_any",
    "must_not_contain",
    "expect_tool",
    "expect_no_tool",
    "max_latency_ms",
    "min_sources",
}
_REQUIRED_CASE_KEYS = {"name", "question", "checks", "why"}

# Cap on how long a single busy-retry waits, independent of what the
# backend reports -- a misbehaving/huge Retry-After header must not hang
# the suite for minutes. Mirrors the same cap the orchestrator's own
# callers apply per the battle plan.
_MAX_RETRY_WAIT_SECONDS = 60.0


@dataclass
class CaseResult:
    name: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model: str = ""
    reply_excerpt: str = ""
    # Mirrors the matching AssistantAnswer fields the integrated orchestrator
    # now reports (guard/cache/fallback), so a run can be read at a glance
    # without re-deriving it from the reply text. `cache_hit` should always
    # be False here -- `run_case` forces the cache off for its own call
    # (see `_eval_config`) -- but it's still reported rather than assumed,
    # in case that ever stops being true for one case.
    request_id: str = ""
    guard_flagged: bool = False
    cache_hit: bool = False
    fell_back: bool = False


def load_cases(path: str | Path | None = None) -> list[dict]:
    """Load and validate `eval_cases.json` (or `path`, for tests). Raises
    `ValueError` for anything a case author could get wrong: a missing
    required key, an unknown check key, an empty `checks` dict, or a
    duplicate case name -- all at load time, before any model call, so a
    typo fails fast instead of silently no-op'ing a check."""
    import json

    p = Path(path) if path is not None else _DEFAULT_CASES_PATH
    with p.open("r", encoding="utf-8") as f:
        cases = json.load(f)

    if not isinstance(cases, list):
        raise ValueError(f"{p}: expected a JSON list of eval cases")

    seen_names: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError(f"{p}: every eval case must be a JSON object")
        missing = _REQUIRED_CASE_KEYS - case.keys()
        if missing:
            raise ValueError(
                f"eval case {case.get('name', '?')!r} is missing required key(s): "
                f"{sorted(missing)}"
            )
        name = case["name"]
        if name in seen_names:
            raise ValueError(f"duplicate eval case name: {name!r}")
        seen_names.add(name)

        checks = case["checks"]
        if not isinstance(checks, dict) or not checks:
            raise ValueError(f"eval case {name!r} must have a non-empty 'checks' object")
        unknown = set(checks) - _KNOWN_CHECK_KEYS
        if unknown:
            raise ValueError(f"eval case {name!r} has unknown check key(s): {sorted(unknown)}")

    return cases


def _check_reply(checks: dict, result, latency_ms: float) -> list[str]:
    failures: list[str] = []
    reply_low = (result.reply or "").lower()
    tool_names = [entry.get("tool") for entry in result.tool_trace]

    if "must_contain_any" in checks:
        options = checks["must_contain_any"]
        if not any(opt.lower() in reply_low for opt in options):
            failures.append(f"reply contained none of {options!r}")

    if "must_not_contain" in checks:
        hit = [bad for bad in checks["must_not_contain"] if bad.lower() in reply_low]
        if hit:
            failures.append(f"reply contained forbidden text {hit!r}")

    if "expect_tool" in checks:
        want = checks["expect_tool"]
        if want not in tool_names:
            failures.append(f"expected tool {want!r} to be called; tools called: {tool_names!r}")

    if "expect_no_tool" in checks:
        spec = checks["expect_no_tool"]
        if spec is True:
            if tool_names:
                failures.append(f"expected no tool calls; tools called: {tool_names!r}")
        else:
            forbidden_hit = [t for t in tool_names if t in spec]
            if forbidden_hit:
                failures.append(f"forbidden tool(s) called: {forbidden_hit!r}")

    if "max_latency_ms" in checks:
        cap = checks["max_latency_ms"]
        if latency_ms > cap:
            failures.append(f"latency {latency_ms:.0f}ms exceeded cap {cap}ms")

    if "min_sources" in checks:
        want = checks["min_sources"]
        if len(result.sources) < want:
            failures.append(f"expected at least {want} source(s); got {len(result.sources)}")

    return failures


def _eval_config(config: Any) -> dict:
    """A plain-dict copy of `config` with the repeat-answer cache forced
    off, for the eval suite's own `answer()` calls only.

    The orchestrator now caches the first turn of an anonymous, tool-free
    conversation (app/services/assistant/cache.py) -- exactly the shape
    most eval cases are. Without this, the *second* time a live run asked
    "Walk me through the trading simulator project", it would get served
    straight out of Redis: zero tokens, zero model call, and zero chance
    of catching a regression, which defeats the entire point of a suite
    meant to hit the real model. A dict copy, not a mutation of `config`
    itself, since callers normally pass `app.config`, which is shared
    process-wide -- running evals must not leave the cache permanently
    disabled for real traffic. Every reader downstream (`answer()`,
    `cache.py`, `guard.py`, `build_backend`, ...) reads config with `[...]`
    or `.get(...)`, both of which a plain dict supports identically to
    Flask's `Config`."""
    cfg = dict(config)
    cfg["ASSISTANT_CACHE_ENABLED"] = False
    return cfg


def run_case(case: dict, config: Any) -> CaseResult:
    """Run one case against `answer()` and check its result. A busy backend
    (`AssistantUnavailable` with a truthy `retry_after`, e.g.
    `AssistantBusy`) is retried exactly once, after sleeping
    `min(retry_after, 60s)` -- `getattr` on purpose, tolerant of any
    `AssistantUnavailable` subclass that carries the attribute. Any other
    `AssistantUnavailable`, or a busy retry that fails again, is recorded
    as a failing case rather than raised, so one flaky case never aborts
    the rest of the suite. Every call goes through `_eval_config` so this
    suite always exercises the real model, never a cached answer."""
    question = case["question"]
    history = case.get("history") or []
    name = case["name"]
    cfg = _eval_config(config)

    start = time.monotonic()
    try:
        result = answer(
            question, history, config=cfg, is_admin=False, job_tools_authorized=False
        )
    except AssistantUnavailable as exc:
        retry_after = getattr(exc, "retry_after", None)
        if not retry_after:
            latency_ms = (time.monotonic() - start) * 1000
            return CaseResult(
                name=name,
                passed=False,
                failures=[f"assistant unavailable: {exc}"],
                latency_ms=latency_ms,
            )
        time.sleep(min(float(retry_after), _MAX_RETRY_WAIT_SECONDS))
        try:
            result = answer(
                question, history, config=cfg, is_admin=False, job_tools_authorized=False
            )
        except Exception as exc2:  # noqa: BLE001 - report, don't crash the suite
            latency_ms = (time.monotonic() - start) * 1000
            return CaseResult(
                name=name,
                passed=False,
                failures=[f"still unavailable after busy-retry: {exc2}"],
                latency_ms=latency_ms,
            )

    latency_ms = (time.monotonic() - start) * 1000
    failures = _check_reply(case["checks"], result, latency_ms)
    return CaseResult(
        name=name,
        passed=not failures,
        failures=failures,
        latency_ms=latency_ms,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        model=result.model,
        reply_excerpt=(result.reply or "")[:160],
        request_id=result.request_id,
        guard_flagged=result.guard_flagged,
        cache_hit=result.cache_hit,
        fell_back=result.fell_back,
    )


def run_suite(config: Any, names: list[str] | None = None, pace: bool = True) -> list[CaseResult]:
    """Run every case in `eval_cases.json` (or just `names`, if given) and
    return one `CaseResult` per case, in order.

    Pacing: between cases (never before the first), sleep long enough that
    the *previous* case's total tokens, spent again every case at this
    suite's cadence, would stay under `config.get("ASSISTANT_EVAL_TPM",
    7000)` tokens/minute -- i.e. `sleep = prev_tokens / tpm * 60`. This is
    deliberately based on the case that just ran, not a fixed interval,
    because a plain turn and a tool-heavy turn cost very different amounts
    of quota and the point is to protect the token budget, not the clock.
    Skipped entirely when `pace` is False or `config["TESTING"]` is set --
    the offline scripted backend the test suite uses spends no real quota,
    so there is nothing to pace against."""
    cases = load_cases()
    if names:
        wanted = set(names)
        available = {c["name"] for c in cases}
        missing = wanted - available
        if missing:
            raise ValueError(f"unknown eval case name(s): {sorted(missing)}")
        cases = [c for c in cases if c["name"] in wanted]

    tpm = float(config.get("ASSISTANT_EVAL_TPM", 7000))
    testing = bool(config.get("TESTING"))

    results: list[CaseResult] = []
    for case in cases:
        if pace and not testing and results:
            prev = results[-1]
            prev_tokens = (prev.prompt_tokens or 0) + (prev.completion_tokens or 0)
            if prev_tokens and tpm > 0:
                time.sleep(prev_tokens / tpm * 60.0)
        results.append(run_case(case, config))
    return results

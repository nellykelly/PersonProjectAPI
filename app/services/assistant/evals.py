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

import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import AssistantUnavailable
from .orchestrator import answer

_DEFAULT_CASES_PATH = Path(__file__).with_name("eval_cases.json")
_DEFAULT_BASELINE_PATH = Path(__file__).with_name("eval_baseline.json")

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


def _git_commit() -> str | None:
    """Best-effort short commit hash for the checkout that ran the suite,
    e.g. so a later regression can be lined up against what changed. Must
    never raise or abort a real eval run over something this cosmetic --
    a missing `git` binary, a non-repo checkout (some deploy images), or
    any other failure all just mean "unknown", not a crash."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001 - never let this abort a real run
        return None
    if proc.returncode != 0:
        return None
    commit = proc.stdout.strip()
    return commit or None


def record_run(
    results: list[CaseResult],
    config: Any,
    started_at: datetime,
    finished_at: datetime,
) -> "AssistantEvalRun":  # noqa: F821 - imported lazily below
    """Persist one `flask assistant eval` invocation: one AssistantEvalRun
    row summarizing the whole run, plus one AssistantEvalCaseResult row
    per case in `results`. Purely additive -- this is the eval suite's
    only write path into the database; nothing above (`run_case`,
    `run_suite`, the existing checks) changes.

    `model` on the run is the last case result's model (whatever answered
    last), or "unknown" if `results` is empty -- there is no single
    "the" model otherwise (a run could span a fallback mid-suite).
    """
    from app.extensions import db
    from app.models import AssistantEvalCaseResult, AssistantEvalRun

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    total_tokens = sum((r.prompt_tokens or 0) + (r.completion_tokens or 0) for r in results)
    avg_latency_ms = (sum(r.latency_ms for r in results) / total) if total else None
    model = results[-1].model if results else "unknown"

    run = AssistantEvalRun(
        started_at=started_at,
        finished_at=finished_at,
        git_commit=_git_commit(),
        model=model,
        total_cases=total,
        passed_cases=passed,
        failed_cases=failed,
        avg_latency_ms=avg_latency_ms,
        total_tokens=total_tokens,
    )
    db.session.add(run)
    db.session.flush()  # assign run.id before writing the case rows

    for r in results:
        db.session.add(
            AssistantEvalCaseResult(
                run_id=run.id,
                case_name=r.name,
                passed=r.passed,
                failures="\n".join(r.failures) if r.failures else None,
                latency_ms=r.latency_ms,
                tokens=(r.prompt_tokens or 0) + (r.completion_tokens or 0),
                request_id=r.request_id or None,
                cache_hit=r.cache_hit,
                guard_flagged=r.guard_flagged,
                fell_back=r.fell_back,
            )
        )
    db.session.commit()
    return run


# ---------------------------------------------------------------------------
# Regression gate: compare a run's pass rate against a committed baseline.
#
# The suite above (and `flask assistant eval`) already fails loudly on any
# single case -- but every case here hits the real, live model, so a lone
# flaky response (a borderline phrasing, a slow network blip against
# `max_latency_ms`) can fail the whole run even with zero real regression.
# A committed baseline plus a percentage-point threshold answers a
# different, coarser question that's actually safe to gate a deploy on:
# "did quality meaningfully drop", not "did literally every case pass this
# one run". `eval_baseline.json` is deliberately just the pass rate and
# which cases failed -- not full CaseResult detail -- so that updating it
# after a real, reviewed improvement is a small, readable diff; per-run
# detail already lives in AssistantEvalRun/AssistantEvalCaseResult.
# ---------------------------------------------------------------------------


def _pass_rate(results: list[CaseResult]) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if r.passed) / len(results)


def build_baseline(results: list[CaseResult]) -> dict:
    """A plain, git-diffable summary of one eval run, meant to be written to
    `eval_baseline.json` (via `write_baseline`) and committed."""
    from app.models import utcnow

    failing = sorted(r.name for r in results if not r.passed)
    return {
        "total_cases": len(results),
        "passed_cases": len(results) - len(failing),
        "pass_rate": round(_pass_rate(results), 4),
        "failing_cases": failing,
        "recorded_at": utcnow().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
    }


def load_baseline(path: str | Path | None = None) -> dict | None:
    """Return the committed baseline dict, or None if it doesn't exist yet
    (before the first `flask assistant eval-baseline --write`). Never
    raises on a missing file -- `eval-gate` treats that as "nothing to
    compare against yet", not a crash, so a fresh checkout doesn't need the
    file to exist just to run the suite once."""
    p = Path(path) if path is not None else _DEFAULT_BASELINE_PATH
    if not p.exists():
        return None
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_baseline(baseline: dict, path: str | Path | None = None) -> Path:
    """Write `baseline` (from `build_baseline`) to disk as pretty, sorted,
    newline-terminated JSON -- stable formatting so re-running with an
    unchanged result produces a no-op git diff instead of key-order churn."""
    p = Path(path) if path is not None else _DEFAULT_BASELINE_PATH
    with p.open("w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, sort_keys=True)
        f.write("\n")
    return p


# Case-naming prefixes that make up the prompt-injection suite: direct
# injection (prompt_injection_*, the original category), indirect injection
# (hostile text inside a pasted document or a tool result), tool misuse
# (skipping the preview/confirm flow, claiming owner identity, naming a
# tool that doesn't exist), and false claims about Nelson specifically
# (distinct from the general grounding_invented_* fabrication cases -- these
# are damaging claims to *correct*, not just facts to decline on). Keyed off
# naming convention rather than a field on each case, so a case joins a
# category just by being named with the right prefix -- no schema change.
_SECURITY_CATEGORY_PREFIXES = [
    "prompt_injection",
    "injection_indirect",
    "tool_misuse",
    "false_claim",
]


def security_category_breakdown(results: list[CaseResult]) -> dict[str, tuple[int, int]]:
    """{category_prefix: (passed, total)} for every case in `results` whose
    name matches one of `_SECURITY_CATEGORY_PREFIXES`, in no particular
    order. This is the per-category pass-rate breakout the general
    pass/fail table doesn't give on its own -- printed by `flask assistant
    eval`/`eval-gate` after every run, so the trend is visible run over run
    in each run's own console output and in `AssistantEvalCaseResult`
    (case_name + passed + the run's started_at already let this same
    breakdown be reconstructed for any past run, with no new column)."""
    counts: dict[str, list[int]] = {}
    for r in results:
        prefix = next((p for p in _SECURITY_CATEGORY_PREFIXES if r.name.startswith(p)), None)
        if prefix is None:
            continue
        bucket = counts.setdefault(prefix, [0, 0])
        bucket[1] += 1
        if r.passed:
            bucket[0] += 1
    return {k: (v[0], v[1]) for k, v in counts.items()}


@dataclass
class GateVerdict:
    regressed: bool
    current_pass_rate: float
    baseline_pass_rate: float | None
    threshold: float
    newly_failing: list[str]
    message: str


def check_regression(
    results: list[CaseResult], baseline: dict | None, threshold: float
) -> GateVerdict:
    """Decide whether `results` represents a regression against `baseline`.

    `baseline=None` (no committed file yet) is never a regression -- there
    is nothing to compare against, so the gate passes and says so, rather
    than failing a first-time setup. Otherwise, regressed iff the drop in
    pass rate (baseline minus current) exceeds `threshold`. `newly_failing`
    lists cases that fail now but did not fail in the baseline -- useful in
    the gate's output even when the overall drop doesn't cross the
    threshold, since one new failure in a 35-case suite is a ~3% drop that
    a 10%-threshold gate would let through silently otherwise.
    """
    current = _pass_rate(results)
    if baseline is None:
        return GateVerdict(
            regressed=False,
            current_pass_rate=current,
            baseline_pass_rate=None,
            threshold=threshold,
            newly_failing=[],
            message=(
                "no committed baseline yet -- run "
                "`flask assistant eval-baseline --write` to create one"
            ),
        )

    base_rate = float(baseline.get("pass_rate", 0.0))
    previously_failing = set(baseline.get("failing_cases", []))
    newly_failing = sorted(
        r.name for r in results if not r.passed and r.name not in previously_failing
    )
    drop = base_rate - current
    regressed = drop > threshold
    if regressed:
        message = (
            f"pass rate dropped {drop:.1%} (baseline {base_rate:.1%} -> "
            f"current {current:.1%}), exceeding the {threshold:.1%} threshold"
        )
    else:
        message = f"pass rate {current:.1%} within {threshold:.1%} of baseline {base_rate:.1%}"
    return GateVerdict(
        regressed=regressed,
        current_pass_rate=current,
        baseline_pass_rate=base_rate,
        threshold=threshold,
        newly_failing=newly_failing,
        message=message,
    )

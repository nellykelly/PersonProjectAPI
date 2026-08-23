"""Pipeline World's real 7-stage pipeline: sanitize, security_scan,
test_uniqueness, test_profanity, build, deploy, verify.

Each stage maps to one concrete concern (see models.py's PIPELINE_STAGES
comment for the full reasoning) rather than one arbitrary CI-flavored
label per phase. This is a real RQ job (see queue.py), not a fake
progress bar: each stage records a real PipelineRun row, pushes a real
SocketIO event -- including the actual pseudo-commands it's running, for
the live build-log feed on the tracker page -- and a failure at any
stage stops the character there, visibly, with a reason, rather than
continuing or silently erroring.

`run_pipeline` is the RQ job entrypoint. It needs an app context, which
the caller (the in-process fallback worker, or the real `worker.py`
process) is responsible for pushing -- see queue.py's module docstring.
"""
from __future__ import annotations

import random
import time

from flask import current_app

from app.extensions import db, socketio
from app.models import PIPELINE_STAGES, Character, PipelineRun, utcnow
from app.services import queue as queue_service
from app.services import validators, world_cache

SOCKETIO_NAMESPACE = "/pipeline-world"
SOCKETIO_EVENT = "pipeline_update"
MODE_EVENT = "pipeline_mode_update"
BENCHMARKS_EVENT = "pipeline_benchmarks_update"

FAST_MODE_REDIS_KEY = "pipeline_world:fast_mode"

# When fast mode is off, each stage gets its own randomly-rolled delay in
# this range (re-rolled fresh per stage, per flow) so the demo is
# watchable -- a fixed delay made every run look identical and robotic.
SLOW_MODE_DELAY_RANGE = (1.0, 10.0)


def is_fast_mode() -> bool:
    # Tests must never depend on incidental Redis state (fakeredis is
    # shared across the whole test session) -- always fast under TESTING,
    # same guarantee TestingConfig's PIPELINE_STAGE_DELAY_SECONDS=0 gave
    # before this toggle existed.
    if current_app.config.get("TESTING"):
        return True
    conn = queue_service.get_redis_connection()
    return conn.get(FAST_MODE_REDIS_KEY) == b"1"


def set_fast_mode(enabled: bool) -> None:
    conn = queue_service.get_redis_connection()
    conn.set(FAST_MODE_REDIS_KEY, "1" if enabled else "0")


def format_duration_seconds(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    if seconds < 1:
        return f"{round(seconds * 1000)}ms"
    return f"{seconds:.2f}s"


def fast_mode_benchmarks() -> list[dict]:
    """Real per-stage timing, averaged across every stage that has ever
    actually run with the artificial delay disabled -- deliberately
    excludes slow-mode runs, whose recorded duration includes that
    artificial sleep and would make the numbers meaningless as a
    benchmark of real processing time."""
    runs = PipelineRun.query.filter_by(fast_mode=True).all()
    samples_by_stage: dict[str, list[float]] = {stage: [] for stage in PIPELINE_STAGES}
    for run in runs:
        duration = run.duration_seconds
        if duration is not None and run.stage in samples_by_stage:
            samples_by_stage[run.stage].append(duration)

    benchmarks = []
    for stage in PIPELINE_STAGES:
        samples = samples_by_stage[stage]
        avg_seconds = (sum(samples) / len(samples)) if samples else None
        min_seconds = min(samples) if samples else None
        max_seconds = max(samples) if samples else None
        benchmarks.append(
            {
                "stage": stage,
                "samples": len(samples),
                "avg_seconds": avg_seconds,
                "min_seconds": min_seconds,
                "max_seconds": max_seconds,
                "avg_display": format_duration_seconds(avg_seconds),
                "min_display": format_duration_seconds(min_seconds),
                "max_display": format_duration_seconds(max_seconds),
            }
        )
    return benchmarks


def emit_benchmarks_update() -> None:
    """Split out from run_pipeline's finally block for the same reason
    emit_stage_update is its own function -- tests can monkeypatch this
    one call instead of needing a real Socket.IO client."""
    socketio.emit(BENCHMARKS_EVENT, {"benchmarks": fast_mode_benchmarks()}, namespace=SOCKETIO_NAMESPACE)


# What each stage "runs," shown in the live build-log feed the instant a
# stage starts. These are illustrative of the real check being performed
# (and for test_uniqueness/deploy/verify, literally the shape of the
# actual SQL) rather than the exact executed statement -- the point is
# giving a visitor honest, concrete feedback about *what kind of thing*
# is happening at each stage, the same way a CI log line tells you what
# command is running even summarized.
STAGE_COMMANDS = {
    "sanitize": [
        "strip_whitespace(name)",
        "enforce_charset(name)  # letters, spaces, hyphens, apostrophes only",
        "enforce_max_length(name, 30)",
        "validate_option(appearance_id, head_type_id, body_type_id, hand_type_id)",
        "enforce_charset(icebreaker_answer) for each of the 4 fixed questions",
        "enforce_max_length(icebreaker_answer, 80) for each of the 4 fixed questions",
    ],
    "security_scan": [
        "scan_for_html_script_tags(raw_input)",
        "scan_for_sql_metacharacters(raw_input)",
        "scan_for_html_script_tags(icebreaker_answer) for each of the 4 fixed questions",
    ],
    "test_uniqueness": [
        "SELECT id FROM characters WHERE lower(full_name) = lower(:name) AND status != 'failed'",
    ],
    "test_profanity": [
        "sha256(token) in BLOCKED_NAME_HASHES for token in name_part",
        "sha256(token) in BLOCKED_NAME_HASHES for token in icebreaker_answer, for each of the 4 fixed questions",
    ],
    "build": [
        "assign_world_position(bounds=PRODUCTION_TOWN_BOUNDS)",
        "lookup_appearance_render_data(appearance_id)",
        "compose_speech_bubble_text(icebreaker_answers)  # all 4 fixed Q&A pairs",
    ],
    "deploy": [
        "UPDATE characters SET status='live', world_x=:x, world_y=:y WHERE id=:id",
    ],
    "verify": [
        "SELECT * FROM characters WHERE id=:id AND status='live'",
    ],
}


def emit_stage_update(
    character: Character, stage: str, status: str, detail: str | None = None, duration_seconds: float | None = None
) -> None:
    """status is one of 'start' | 'pass' | 'fail'. Split into its own
    function (rather than inlined per call site) so tests can monkeypatch
    or assert on it without needing a real Socket.IO client."""
    payload = {
        "character": character.to_dict(),
        "stage": stage,
        "status": status,
        "detail": detail,
        "duration_seconds": duration_seconds,
    }
    if status == "start":
        payload["commands"] = STAGE_COMMANDS.get(stage, [])
    socketio.emit(SOCKETIO_EVENT, payload, namespace=SOCKETIO_NAMESPACE)


def _record_stage(
    character_id: int, stage: str, status: str, detail: str | None, started_at, fast_mode: bool
) -> PipelineRun:
    ended_at = utcnow()
    run = PipelineRun(
        character_id=character_id,
        stage=stage,
        status=status,
        detail=detail,
        started_at=started_at,
        ended_at=ended_at,
        fast_mode=fast_mode,
    )
    db.session.add(run)
    db.session.commit()
    return run


def _fail(character: Character, stage: str, reason: str, started_at, fast_mode: bool) -> None:
    character.status = "failed"
    character.failure_reason = reason
    db.session.commit()
    run = _record_stage(character.id, stage, "fail", reason, started_at, fast_mode)
    # Defensive, not just for stages before Deploy: if Verify is ever the
    # one that fails, Deploy already wrote this character live and
    # invalidated the cache once -- make sure a stale "live" snapshot
    # doesn't linger after we just un-lived them.
    world_cache.invalidate_world_cache()
    emit_stage_update(character, stage, "fail", reason, run.duration_seconds)


def _pass_stage(
    character: Character,
    stage: str,
    next_status: str | None,
    started_at,
    fast_mode: bool,
    detail: str | None = None,
) -> None:
    """`next_status=None` means "this stage already managed
    character.status itself" (Deploy writes 'live' directly, Verify
    doesn't change it at all) -- don't stomp on that here."""
    if next_status is not None:
        character.status = next_status
        db.session.commit()
    run = _record_stage(character.id, stage, "pass", detail, started_at, fast_mode)
    emit_stage_update(character, stage, "pass", detail, run.duration_seconds)


def _spawn_position() -> tuple[float, float]:
    import random

    from app.config import PRODUCTION_TOWN_BOUNDS

    x0, y0, x1, y1 = PRODUCTION_TOWN_BOUNDS
    return round(random.uniform(x0, x1), 1), round(random.uniform(y0, y1), 1)


def _stage_delay(fast_mode: bool) -> float:
    # Escape hatch, not the normal path: an explicit env override still
    # wins if set (TestingConfig uses this to force 0 independently of
    # is_fast_mode()'s own TESTING check). Normal operation never sets it.
    override = current_app.config.get("PIPELINE_STAGE_DELAY_SECONDS")
    if override is not None:
        return float(override)
    return 0.0 if fast_mode else random.uniform(*SLOW_MODE_DELAY_RANGE)


def _run_stage(
    character: Character, stage: str, next_status: str | None, check_fn, pass_detail: str, fast_mode: bool
) -> bool:
    """Runs one stage's check(s): emits 'start' (with its commands),
    sleeps the stage's delay (0 in fast mode, otherwise a fresh random
    1-10s roll), runs `check_fn` (which raises ValidationError on
    failure), then emits pass/fail. Returns True to continue to the next
    stage, False if the pipeline should stop here.

    Every stage has to end in a real, visible pass or fail -- there's no
    third option. Originally only `ValidationError` was caught here, on
    the assumption that's the only way a check function fails; in
    practice a stage can also just crash (a real bug, a DB hiccup, the
    worker process itself getting killed mid-job by a redeploy). That
    bypassed this function's except clause entirely: no PipelineRun row
    got written, no 'fail' event fired, and -- worse -- for Verify
    specifically, which runs *after* Deploy already committed
    status='live', a crashed Verify left a character stranded live in
    the world with a blank, un-investigable gap in its own pipeline
    history instead of a visible failure. Caught live in production.
    Any exception, not just ValidationError, now ends the stage the
    same way: failed, visibly, with the character un-lived if Deploy
    had already gotten that far (see _fail's world_cache invalidation).
    """
    delay = _stage_delay(fast_mode)
    emit_stage_update(character, stage, "start")
    started_at = utcnow()
    time.sleep(delay)
    try:
        check_fn()
    except validators.ValidationError as exc:
        _fail(character, stage, str(exc), started_at, fast_mode)
        return False
    except Exception:
        # Deliberately generic, not the raw exception text: a visitor-
        # facing pipeline log is not the place to leak internal
        # tracebacks. The real detail goes to the app logger instead.
        current_app.logger.exception(
            "Pipeline stage %r crashed for character %s -- recording as a failure instead of "
            "leaving no PipelineRun row at all",
            stage,
            character.id,
        )
        _fail(character, stage, "internal error -- this stage did not complete", started_at, fast_mode)
        return False
    _pass_stage(character, stage, next_status, started_at, fast_mode, pass_detail)
    return True


def run_pipeline(character_id: int) -> None:
    character = db.session.get(Character, character_id)
    if character is None:
        return  # deleted/bad id shouldn't crash the worker

    # Decided once per flow, not re-checked stage-by-stage -- a toggle
    # flipped mid-run shouldn't change behavior partway through a run
    # that's already in flight.
    fast_mode = is_fast_mode()

    character.status = "sanitizing"
    db.session.commit()

    try:
        def _sanitize():
            validators.sanitize_name_part(character.first_name, "First name")
            validators.sanitize_name_part(character.last_name, "Last name")
            validators.validate_appearance_id(character.appearance_id)
            validators.validate_head_type_id(character.head_type_id)
            validators.validate_body_type_id(character.body_type_id)
            validators.validate_hand_type_id(character.hand_type_id)
            for question in validators.FIXED_ICEBREAKER_QUESTIONS:
                validators.sanitize_icebreaker_answer(getattr(character, question["field_name"]))

        if not _run_stage(character, "sanitize", "scanning", _sanitize, "format/length/charset OK", fast_mode):
            return

        def _security_scan():
            validators.check_no_injection_patterns(character.first_name, "First name")
            validators.check_no_injection_patterns(character.last_name, "Last name")
            for question in validators.FIXED_ICEBREAKER_QUESTIONS:
                validators.check_no_injection_patterns(
                    getattr(character, question["field_name"]) or "", "Icebreaker answer"
                )

        if not _run_stage(
            character, "security_scan", "testing_uniqueness", _security_scan, "no injection patterns found", fast_mode
        ):
            return

        def _test_uniqueness():
            validators.check_full_name_collision(
                character.first_name, character.last_name, exclude_character_id=character.id
            )

        if not _run_stage(
            character, "test_uniqueness", "testing_profanity", _test_uniqueness, "name is unique", fast_mode
        ):
            return

        def _test_profanity():
            validators.check_no_profanity(character.first_name, "First name")
            validators.check_no_profanity(character.last_name, "Last name")
            for question in validators.FIXED_ICEBREAKER_QUESTIONS:
                validators.check_no_profanity(getattr(character, question["field_name"]) or "", "Icebreaker answer")

        if not _run_stage(
            character, "test_profanity", "building", _test_profanity, "no blocked words found", fast_mode
        ):
            return

        def _build():
            character.world_x, character.world_y = _spawn_position()
            db.session.commit()

        if not _run_stage(character, "build", "deploying", _build, "spawn payload assembled", fast_mode):
            return

        def _deploy():
            # This *is* the deploy action -- writing status="live" to the row.
            # next_status=None below so _pass_stage doesn't then overwrite it.
            character.status = "live"
            db.session.commit()
            world_cache.invalidate_world_cache()

        if not _run_stage(character, "deploy", None, _deploy, "written as live", fast_mode):
            return

        def _verify():
            # A genuine read-after-write check, not assumed -- re-reads the
            # row rather than trusting the in-memory object still matches
            # what was committed.
            db.session.refresh(character)
            if character.status != "live":
                raise validators.ValidationError("post-deploy verification found an unexpected status")

        _run_stage(character, "verify", None, _verify, "read-after-write check passed", fast_mode)
    finally:
        # Recalculated and re-broadcast every time a run happens in fast
        # mode -- not just on page load -- so every open tab's benchmarks
        # table stays current without a reload. Runs on every exit path
        # (including an early stage failure), not just full success.
        if fast_mode:
            emit_benchmarks_update()

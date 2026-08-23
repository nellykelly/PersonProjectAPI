import pytest

from app.extensions import db
from app.models import Character, PipelineRun
from app.services import pipeline

ALL_STAGES = ["sanitize", "security_scan", "test_uniqueness", "test_profanity", "build", "deploy", "verify"]

# Captured before any test runs / monkeypatches -- the autouse
# capture_socketio_emits fixture below replaces the *module attribute*
# pipeline.emit_stage_update for every test in this file, so a test that
# wants the real implementation (to check its socketio.emit payload
# shape) needs a reference to the original function object, not the name.
_REAL_EMIT_STAGE_UPDATE = pipeline.emit_stage_update

DEFAULT_ANSWERS = {"food": "Tacos", "movie": "Inception", "hobby": "Reading", "weekend": "Hiking"}


def _make_character(
    session_id="s1",
    first="Nelson",
    last="Koskela",
    appearance="sky",
    head_type="round_tan",
    body_type="regular",
    hand_type="bare",
    **answer_overrides,
):
    answers = dict(DEFAULT_ANSWERS)
    answers.update(answer_overrides)
    character = Character(
        session_id=session_id,
        first_name=first,
        last_name=last,
        appearance_id=appearance,
        head_type_id=head_type,
        body_type_id=body_type,
        hand_type_id=hand_type,
        icebreaker_answer_food=answers["food"],
        icebreaker_answer_movie=answers["movie"],
        icebreaker_answer_hobby=answers["hobby"],
        icebreaker_answer_weekend=answers["weekend"],
    )
    db.session.add(character)
    db.session.commit()
    return character


@pytest.fixture(autouse=True)
def capture_socketio_emits(monkeypatch):
    events = []
    monkeypatch.setattr(
        pipeline,
        "emit_stage_update",
        lambda character, stage, status, detail=None, duration_seconds=None: events.append(
            {
                "character_id": character.id,
                "stage": stage,
                "status": status,
                "detail": detail,
                "duration_seconds": duration_seconds,
            }
        ),
    )
    monkeypatch.setattr(pipeline, "emit_benchmarks_update", lambda: None)
    return events


def test_happy_path_reaches_live_and_spawns_in_bounds(app, db, capture_socketio_emits):
    from app.config import PRODUCTION_TOWN_BOUNDS

    character = _make_character()
    pipeline.run_pipeline(character.id)

    db.session.refresh(character)
    assert character.status == "live"
    assert character.failure_reason is None

    x0, y0, x1, y1 = PRODUCTION_TOWN_BOUNDS
    assert x0 <= character.world_x <= x1
    assert y0 <= character.world_y <= y1

    stages_seen = [e["stage"] for e in capture_socketio_emits if e["status"] == "pass"]
    assert stages_seen == ALL_STAGES


def test_happy_path_records_a_pipeline_run_per_stage(app, db):
    character = _make_character()
    pipeline.run_pipeline(character.id)

    runs = PipelineRun.query.filter_by(character_id=character.id).order_by(PipelineRun.started_at).all()
    assert [r.stage for r in runs] == ALL_STAGES
    assert all(r.status == "pass" for r in runs)


# ---------- Sanitize stage: format/hygiene only ----------


def test_bad_name_format_fails_at_sanitize_stage(app, db):
    character = _make_character(first="Nelson123")
    pipeline.run_pipeline(character.id)

    db.session.refresh(character)
    assert character.status == "failed"
    run = PipelineRun.query.filter_by(character_id=character.id).first()
    assert run.stage == "sanitize"


def test_invalid_appearance_id_fails_at_sanitize_stage(app, db):
    character = _make_character(appearance="not-real")
    pipeline.run_pipeline(character.id)

    db.session.refresh(character)
    assert character.status == "failed"
    run = PipelineRun.query.filter_by(character_id=character.id).first()
    assert run.stage == "sanitize"


def test_invalid_head_type_id_fails_at_sanitize_stage(app, db):
    character = _make_character(head_type="not-real")
    pipeline.run_pipeline(character.id)

    db.session.refresh(character)
    assert character.status == "failed"
    run = PipelineRun.query.filter_by(character_id=character.id).first()
    assert run.stage == "sanitize"


def test_invalid_body_type_id_fails_at_sanitize_stage(app, db):
    character = _make_character(body_type="not-real")
    pipeline.run_pipeline(character.id)

    db.session.refresh(character)
    assert character.status == "failed"
    run = PipelineRun.query.filter_by(character_id=character.id).first()
    assert run.stage == "sanitize"


def test_invalid_hand_type_id_fails_at_sanitize_stage(app, db):
    character = _make_character(hand_type="not-real")
    pipeline.run_pipeline(character.id)

    db.session.refresh(character)
    assert character.status == "failed"
    run = PipelineRun.query.filter_by(character_id=character.id).first()
    assert run.stage == "sanitize"


def test_missing_icebreaker_answer_fails_at_sanitize_stage(app, db):
    character = _make_character(hobby=None)
    pipeline.run_pipeline(character.id)

    db.session.refresh(character)
    assert character.status == "failed"
    run = PipelineRun.query.filter_by(character_id=character.id).first()
    assert run.stage == "sanitize"


def test_bad_icebreaker_answer_format_fails_at_sanitize_stage(app, db):
    character = _make_character(movie="A" * 81)
    pipeline.run_pipeline(character.id)

    db.session.refresh(character)
    assert character.status == "failed"
    run = PipelineRun.query.filter_by(character_id=character.id).first()
    assert run.stage == "sanitize"


# ---------- Security Scan stage ----------


def test_script_tags_fail_at_sanitize_stage_before_reaching_security_scan(app, db):
    # <script>...</script> is already rejected by Sanitize's charset
    # whitelist (no '<' allowed), so it never reaches Security Scan.
    character = _make_character(food="<script>alert(1)</script>")
    pipeline.run_pipeline(character.id)

    db.session.refresh(character)
    assert character.status == "failed"
    runs = PipelineRun.query.filter_by(character_id=character.id).order_by(PipelineRun.started_at).all()
    assert runs[0].stage == "sanitize"


def test_sql_keyword_pattern_fails_at_security_scan_stage_after_passing_sanitize(app, db):
    # All-letters-and-spaces text like this satisfies Sanitize's charset
    # whitelist, so it passes Sanitize -- but Security Scan's independent
    # pattern check still catches the "drop table" keyword pair.
    character = _make_character(food="drop table characters please")
    pipeline.run_pipeline(character.id)

    db.session.refresh(character)
    assert character.status == "failed"
    runs = PipelineRun.query.filter_by(character_id=character.id).order_by(PipelineRun.started_at).all()
    assert [r.stage for r in runs] == ["sanitize", "security_scan"]
    assert runs[0].status == "pass"
    assert runs[1].status == "fail"


# ---------- Test:Uniqueness stage ----------


def test_full_name_collision_fails_at_test_uniqueness_stage(app, db, capture_socketio_emits):
    _make_character(session_id="s1", first="Nelson", last="Koskela")
    duplicate = _make_character(session_id="s2", first="Nelson", last="Koskela")

    pipeline.run_pipeline(duplicate.id)

    db.session.refresh(duplicate)
    assert duplicate.status == "failed"
    assert "already exists" in duplicate.failure_reason

    runs = PipelineRun.query.filter_by(character_id=duplicate.id).order_by(PipelineRun.started_at).all()
    assert [r.stage for r in runs] == ["sanitize", "security_scan", "test_uniqueness"]
    assert runs[0].status == "pass"
    assert runs[1].status == "pass"
    assert runs[2].status == "fail"

    fail_events = [e for e in capture_socketio_emits if e["status"] == "fail"]
    assert len(fail_events) == 1
    assert fail_events[0]["stage"] == "test_uniqueness"


# ---------- Test:Profanity stage ----------


def test_profanity_in_name_fails_at_test_profanity_stage(app, db):
    character = _make_character(first="damn")
    pipeline.run_pipeline(character.id)

    db.session.refresh(character)
    assert character.status == "failed"
    assert "disallowed" in character.failure_reason
    runs = PipelineRun.query.filter_by(character_id=character.id).order_by(PipelineRun.started_at).all()
    assert [r.stage for r in runs] == ["sanitize", "security_scan", "test_uniqueness", "test_profanity"]


def test_profanity_in_icebreaker_answer_fails_at_test_profanity_stage(app, db):
    character = _make_character(food="damn good tacos")
    pipeline.run_pipeline(character.id)

    db.session.refresh(character)
    assert character.status == "failed"
    assert "disallowed" in character.failure_reason
    runs = PipelineRun.query.filter_by(character_id=character.id).order_by(PipelineRun.started_at).all()
    assert runs[-1].stage == "test_profanity"


def test_run_pipeline_on_missing_character_id_does_not_raise(app, db):
    pipeline.run_pipeline(999999)  # should just return quietly


def test_stage_updates_emit_start_and_pass_events(app, db, capture_socketio_emits):
    character = _make_character()
    pipeline.run_pipeline(character.id)

    kinds = [(e["stage"], e["status"]) for e in capture_socketio_emits]
    assert ("sanitize", "start") in kinds
    assert ("sanitize", "pass") in kinds
    assert ("verify", "start") in kinds
    assert ("verify", "pass") in kinds


def test_every_stage_has_at_least_one_command_listed():
    for stage in ALL_STAGES:
        assert len(pipeline.STAGE_COMMANDS.get(stage, [])) > 0


def test_emit_stage_update_includes_commands_only_on_start(app, db, monkeypatch):
    payloads = []
    monkeypatch.setattr(
        pipeline.socketio, "emit", lambda event, payload, namespace=None: payloads.append(payload)
    )
    character = _make_character()

    _REAL_EMIT_STAGE_UPDATE(character, "sanitize", "start")
    _REAL_EMIT_STAGE_UPDATE(character, "sanitize", "pass", "ok")

    assert "commands" in payloads[0]
    assert len(payloads[0]["commands"]) > 0
    assert "commands" not in payloads[1]


# ---------- a stage crashing with something other than ValidationError ----------
#
# _run_stage used to only catch validators.ValidationError -- any other
# exception (a real bug, a DB hiccup, the worker getting killed mid-job)
# escaped the whole pipeline: no PipelineRun row, no fail event, and for
# Verify specifically (which runs after Deploy already committed
# status='live') a character left stranded live in the world with a
# blank, invisible gap in its own pipeline history. Found live in
# production.


def test_unexpected_exception_in_verify_unlives_the_character(app, db, monkeypatch, capture_socketio_emits):
    character = _make_character()

    real_refresh = db.session.refresh

    def _crash_on_refresh(obj):
        if isinstance(obj, Character):
            raise RuntimeError("simulated worker crash mid-verify")
        return real_refresh(obj)

    monkeypatch.setattr(db.session, "refresh", _crash_on_refresh)

    pipeline.run_pipeline(character.id)

    db.session.refresh = real_refresh
    db.session.refresh(character)

    # Deploy still ran and committed 'live' -- Verify crashing afterward
    # must not leave that standing; the character has to come back down.
    assert character.status == "failed"
    assert character.failure_reason == "internal error -- this stage did not complete"


def test_unexpected_exception_in_verify_still_records_a_pipeline_run(app, db, monkeypatch):
    character = _make_character()

    real_refresh = db.session.refresh

    def _crash_on_refresh(obj):
        if isinstance(obj, Character):
            raise RuntimeError("simulated worker crash mid-verify")
        return real_refresh(obj)

    monkeypatch.setattr(db.session, "refresh", _crash_on_refresh)
    pipeline.run_pipeline(character.id)
    db.session.refresh = real_refresh

    verify_run = PipelineRun.query.filter_by(character_id=character.id, stage="verify").first()
    assert verify_run is not None, "a crashed stage must still leave a real PipelineRun row, not a blank gap"
    assert verify_run.status == "fail"


def test_unexpected_exception_in_verify_emits_a_fail_event_not_silence(app, db, monkeypatch, capture_socketio_emits):
    character = _make_character()

    real_refresh = db.session.refresh

    def _crash_on_refresh(obj):
        if isinstance(obj, Character):
            raise RuntimeError("simulated worker crash mid-verify")
        return real_refresh(obj)

    monkeypatch.setattr(db.session, "refresh", _crash_on_refresh)
    pipeline.run_pipeline(character.id)
    db.session.refresh = real_refresh

    verify_events = [e for e in capture_socketio_emits if e["stage"] == "verify"]
    assert any(e["status"] == "fail" for e in verify_events)


def test_unexpected_exception_at_an_earlier_stage_also_fails_cleanly(app, db, monkeypatch):
    # Not just Verify -- any stage crashing should behave the same way.
    character = _make_character()
    monkeypatch.setattr(
        pipeline.validators, "check_no_injection_patterns", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    pipeline.run_pipeline(character.id)

    assert character.status == "failed"
    run = PipelineRun.query.filter_by(character_id=character.id, stage="security_scan").first()
    assert run is not None
    assert run.status == "fail"

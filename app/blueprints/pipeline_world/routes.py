from flask import current_app, jsonify, render_template, request, session

from app.blueprints.pipeline_world import bp
from app.config import PIPELINE_STAGE_INFO, PRODUCTION_TOWN_BOUNDS
from app.extensions import db, limiter, socketio
from app.models import PIPELINE_STAGES, Character
from app.services import analytics, pipeline, queue, validators, world_cache
from app.services.validators import LastNameCollision

RECENT_RUNS_LIMIT = 20


def _session_id() -> str:
    return session.get("session_id") or "anonymous"


def _recent_pipeline_snapshot(limit: int = RECENT_RUNS_LIMIT) -> list[dict]:
    """One row per recently-submitted character, with a pass/fail/None
    (not reached yet) status per stage -- what the build-tracker table
    renders on initial page load, before any live Socket.IO updates."""
    characters = Character.query.order_by(Character.created_at.desc()).limit(limit).all()
    rows = []
    for character in characters:
        by_stage = {run.stage: run for run in character.pipeline_runs}
        rows.append(
            {
                "character": character.to_dict(),
                "stages": {
                    stage: {
                        "status": by_stage[stage].status,
                        "duration_seconds": by_stage[stage].duration_seconds,
                        "duration_display": pipeline.format_duration_seconds(by_stage[stage].duration_seconds),
                    }
                    if stage in by_stage
                    else None
                    for stage in PIPELINE_STAGES
                },
            }
        )
    return rows


@bp.route("")
def index():
    return render_template(
        "pipeline_world/index.html",
        appearance_options=validators.APPEARANCE_OPTIONS,
        head_type_options=validators.HEAD_TYPE_OPTIONS,
        body_type_options=validators.BODY_TYPE_OPTIONS,
        hand_type_options=validators.HAND_TYPE_OPTIONS,
        icebreaker_questions=validators.FIXED_ICEBREAKER_QUESTIONS,
        # The *raw storage* caps, not the validated limits -- the form
        # deliberately lets an over-limit value be typed so it fails
        # visibly at the Sanitize stage instead of being unenterable.
        max_raw_name_part_length=validators.MAX_RAW_NAME_PART_LENGTH,
        max_raw_icebreaker_answer_length=validators.MAX_RAW_ICEBREAKER_ANSWER_LENGTH,
        stage_info=PIPELINE_STAGE_INFO,
        stage_order=PIPELINE_STAGES,
        recent_runs=_recent_pipeline_snapshot(),
        fast_mode=pipeline.is_fast_mode(),
        benchmarks=pipeline.fast_mode_benchmarks(),
    )


@bp.route("/fast-mode", methods=["POST"])
@limiter.limit(lambda: current_app.config["PIPELINE_FAST_MODE_RATE_LIMIT"])
def toggle_fast_mode():
    enabled = request.form.get("enabled") == "1"
    pipeline.set_fast_mode(enabled)
    socketio.emit(pipeline.MODE_EVENT, {"fast_mode": enabled}, namespace=pipeline.SOCKETIO_NAMESPACE)
    return jsonify({"ok": True, "fast_mode": enabled})


@bp.route("/town")
def town():
    return render_template(
        "pipeline_world/town.html",
        production_town_bounds=PRODUCTION_TOWN_BOUNDS,
        live_world=world_cache.get_live_world(),
        appearance_options=validators.APPEARANCE_OPTIONS,
        head_type_options=validators.HEAD_TYPE_OPTIONS,
        body_type_options=validators.BODY_TYPE_OPTIONS,
        hand_type_options=validators.HAND_TYPE_OPTIONS,
    )


@bp.route("/join", methods=["POST"])
@limiter.limit(lambda: current_app.config["PIPELINE_JOIN_RATE_LIMIT"])
def join():
    confirm = request.form.get("confirm_last_name_collision") == "1"
    icebreaker_answers = {
        question["id"]: request.form.get(f"icebreaker_answer_{question['id']}")
        for question in validators.FIXED_ICEBREAKER_QUESTIONS
    }

    # No content validation here, deliberately -- the submission is stored
    # as-typed and every check happens inside the pipeline stage that owns
    # it (see validators.prepare_join_submission for the full reasoning).
    # A bad name doesn't cancel the run before it starts; it produces a run
    # that visibly fails at Sanitize / Security Scan / Test:Profanity, and
    # a failed stage stops the pipeline there so the character never goes
    # live (see pipeline.run_pipeline).
    try:
        first, last, appearance, head_type, body_type, hand_type, answers = validators.prepare_join_submission(
            request.form.get("first_name"),
            request.form.get("last_name"),
            request.form.get("appearance_id"),
            request.form.get("head_type_id"),
            request.form.get("body_type_id"),
            request.form.get("hand_type_id"),
            icebreaker_answers,
            confirm_last_name_collision=confirm,
        )
    except LastNameCollision as collision:
        # Not a rejection -- a confirm prompt. The visitor answering "yes"
        # re-submits with confirm_last_name_collision=1 and proceeds.
        return jsonify(
            {
                "ok": False,
                "needs_confirmation": True,
                "message": str(collision) + " Continue anyway?",
            }
        )

    character = Character(
        session_id=_session_id(),
        first_name=first,
        last_name=last,
        appearance_id=appearance,
        head_type_id=head_type,
        body_type_id=body_type,
        hand_type_id=hand_type,
        status="pending",
        **{
            question["field_name"]: answers[question["id"]]
            for question in validators.FIXED_ICEBREAKER_QUESTIONS
        },
    )
    db.session.add(character)
    db.session.commit()

    queue.enqueue_character_join(character.id)

    return jsonify({"ok": True, "character": character.to_dict()})


@bp.route("/api/character/<int:character_id>")
def api_character_status(character_id):
    character = db.get_or_404(Character, character_id)
    return jsonify({"ok": True, "character": character.to_dict()})


@bp.route("/api/recent-runs")
def api_recent_runs():
    return jsonify({"ok": True, "rows": _recent_pipeline_snapshot()})


@bp.route("/api/world")
def api_world():
    return jsonify({"ok": True, "characters": world_cache.get_live_world()})


@bp.route("/pipeline-analytics")
def pipeline_analytics():
    return render_template("pipeline_world/pipeline_analytics.html", metrics=analytics.run_all_metrics())

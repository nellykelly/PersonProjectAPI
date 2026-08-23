from flask import current_app, jsonify, render_template, request, session

from app.blueprints.timed_squares import bp
from app.extensions import db, limiter
from app.models import TimedSquaresScore
from app.services import validators

LEADERBOARD_SIZE = 10


@bp.route("")
def index():
    top_scores = (
        TimedSquaresScore.query.order_by(TimedSquaresScore.turns_survived.desc(), TimedSquaresScore.id.asc())
        .limit(LEADERBOARD_SIZE)
        .all()
    )
    return render_template("timed_squares/index.html", top_scores=top_scores)


@bp.route("/api/leaderboard")
def api_leaderboard():
    top_scores = (
        TimedSquaresScore.query.order_by(TimedSquaresScore.turns_survived.desc(), TimedSquaresScore.id.asc())
        .limit(LEADERBOARD_SIZE)
        .all()
    )
    return jsonify({"ok": True, "scores": [s.to_dict() for s in top_scores]})


@bp.route("/api/scores", methods=["POST"])
@limiter.limit(lambda: current_app.config["TIMED_SQUARES_SCORE_RATE_LIMIT"])
def submit_score():
    """Banks one completed run on the public leaderboard. No server-side
    replay validation (see TimedSquaresScore's docstring) -- this only
    checks the payload is a plausible integer within a sane bound, and
    sanitizes the display name, the same "real defense is the schema/
    parametrized queries, this is defense-in-depth on top" posture as
    every other public-write endpoint on this site."""
    raw_turns = request.form.get("turns_survived")
    try:
        turns_survived = int(raw_turns)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "turns_survived must be a whole number"}), 400

    max_turns = current_app.config["TIMED_SQUARES_MAX_TURNS"]
    if turns_survived < 0 or turns_survived > max_turns:
        return jsonify({"ok": False, "error": f"turns_survived must be between 0 and {max_turns}"}), 400

    player_name = validators.sanitize_arcade_name(request.form.get("player_name"))

    score = TimedSquaresScore(
        session_id=session.get("session_id") or "anonymous",
        player_name=player_name,
        turns_survived=turns_survived,
    )
    db.session.add(score)
    db.session.commit()

    better_count = TimedSquaresScore.query.filter(TimedSquaresScore.turns_survived > turns_survived).count()
    return jsonify(
        {
            "ok": True,
            "score": score.to_dict(),
            "rank": better_count + 1,
            "made_leaderboard": better_count < LEADERBOARD_SIZE,
        }
    )

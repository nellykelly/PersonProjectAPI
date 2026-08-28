from flask import jsonify, render_template, request
from flask_login import current_user, login_required

from app.blueprints.leetcode import bp
from app.blueprints.leetcode.problems import TOTAL, iter_problems, topic_view
from app.extensions import db
from app.models import LeetCodeProgress

VALID_MARKS = {"yes", "no"}
_VALID_SLUGS = frozenset(p["slug"] for p in iter_problems())


def _user_marks() -> dict:
    """This account's board as {slug: 'yes' | 'no'}. Unmarked problems
    simply have no row."""
    rows = LeetCodeProgress.query.filter_by(user_id=current_user.id).all()
    return {r.slug: r.mark for r in rows}


@bp.route("")
@login_required
def index():
    """The Top Interview 150 tracker. Login-gated: each account keeps its
    own board (LeetCodeProgress rows), rendered into the page here and
    kept in sync via the /api/progress endpoints below."""
    return render_template(
        "leetcode/index.html",
        topics=topic_view(),
        total=TOTAL,
        marks=_user_marks(),
    )


@bp.route("/api/progress", methods=["GET"])
@login_required
def get_progress():
    return jsonify({"ok": True, "marks": _user_marks()})


@bp.route("/api/progress", methods=["POST"])
@login_required
def set_progress():
    """Upsert or clear a single mark. Body: {"slug": <str>, "mark": "yes"|"no"|null}.
    CSRF-protected (X-CSRFToken header) by the app-wide CSRFProtect."""
    data = request.get_json(silent=True) or {}
    slug = data.get("slug")
    mark = data.get("mark")

    if slug not in _VALID_SLUGS:
        return jsonify({"ok": False, "error": "unknown problem"}), 400
    if mark is not None and mark not in VALID_MARKS:
        return jsonify({"ok": False, "error": "mark must be 'yes', 'no' or null"}), 400

    row = LeetCodeProgress.query.filter_by(user_id=current_user.id, slug=slug).first()
    if mark is None:
        if row is not None:
            db.session.delete(row)
    elif row is None:
        db.session.add(LeetCodeProgress(user_id=current_user.id, slug=slug, mark=mark))
    else:
        row.mark = mark
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/progress/reset", methods=["POST"])
@login_required
def reset_progress():
    LeetCodeProgress.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    return jsonify({"ok": True})


@bp.route("/api/progress/import", methods=["POST"])
@login_required
def import_progress():
    """One-shot merge of a local board (the pre-accounts localStorage
    state) into this account, offered right after a first login. Only
    fills in problems the account hasn't marked yet -- never overwrites a
    server mark. Body: {"marks": {slug: "yes"|"no", ...}}."""
    data = request.get_json(silent=True) or {}
    incoming = data.get("marks")
    if not isinstance(incoming, dict):
        return jsonify({"ok": False, "error": "marks must be an object"}), 400

    existing = {
        r.slug for r in LeetCodeProgress.query.filter_by(user_id=current_user.id).all()
    }
    added = 0
    for slug, mark in incoming.items():
        if slug in _VALID_SLUGS and mark in VALID_MARKS and slug not in existing:
            db.session.add(
                LeetCodeProgress(user_id=current_user.id, slug=slug, mark=mark)
            )
            added += 1
    db.session.commit()
    return jsonify({"ok": True, "added": added})

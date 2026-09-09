"""The private household suite at /family.

Gated exactly like /job-tracker: a signed-session flag set by a correct
password POST, nothing rendered to an unlocked request, and **fails
closed** -- if FAMILY_PASSWORD_HASH isn't configured there is no password
that opens the section.

Deliberately separate from the main site: its own standalone templates
(never `{% extends "base.html" %}`), its own stylesheet, `noindex` on
every response (meta tag *and* X-Robots-Tag header), and a robots.txt
Disallow. Not csrf-exempt.

Only the gate and unlock endpoints are reachable while locked. The
calendar / grocery / chat views are thin here; T7 / T8 / T10 fill in the
services and form handlers.
"""
import calendar as _cal
from datetime import date, datetime, timedelta

from flask import (
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from app.blueprints.family import bp
from app.extensions import limiter
from app.models import FamilyChatMessage, FamilyMember
from app.services.family import FamilyError
from app.services.family import calendar as cal_svc
from app.services.family import chat as chat_svc
from app.services.family import grocery as groc_svc

_OFFLINE_MSG = (
    "I can't get to the thinking part of my brain right now. Try again in a bit."
)

SESSION_KEY = "family_unlocked"
ACTIVE_MEMBER_KEY = "family_active_member"
_MEMBER_SLUGS = ("m1", "m2")

# Reachable while locked: the gate itself. Everything else goes through
# _require_unlocked().
_PUBLIC_ENDPOINTS = {"family.gate", "family.unlock"}


def _is_unlocked() -> bool:
    return session.get(SESSION_KEY) is True


def _password_hash() -> str | None:
    return current_app.config.get("FAMILY_PASSWORD_HASH")


def _active_member_slug() -> str:
    slug = session.get(ACTIVE_MEMBER_KEY)
    return slug if slug in _MEMBER_SLUGS else "m1"


def _active_member() -> FamilyMember:
    m = FamilyMember.by_slug(_active_member_slug()) or FamilyMember.by_slug("m1")
    if m is None:
        abort(500, "Family members are not seeded.")
    return m


@bp.after_request
def _noindex(response):
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@bp.before_request
def _require_unlocked():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if not _password_hash():
        # Fail closed: unconfigured means nobody gets in.
        return render_template("family/unlock.html", unavailable=True), 503
    if not _is_unlocked():
        return redirect(url_for("family.gate"))
    return None


@bp.errorhandler(404)
def _family_404(_err):
    # errors/404.html extends base.html; keep a /family 404 in the family theme.
    return render_template("family/404.html"), 404


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------


@bp.route("", methods=["GET"])
def gate():
    if not _password_hash():
        return render_template("family/unlock.html", unavailable=True), 503
    if _is_unlocked():
        return redirect(url_for("family.home"))
    return render_template("family/unlock.html")


@bp.route("/unlock", methods=["POST"])
@limiter.limit(
    lambda: current_app.config["FAMILY_UNLOCK_RATE_LIMIT"],
    deduct_when=lambda response: response.status_code != 302,
)
def unlock():
    password_hash = _password_hash()
    if not password_hash:
        return render_template("family/unlock.html", unavailable=True), 503

    submitted = request.form.get("password") or ""
    if check_password_hash(password_hash, submitted):
        session[SESSION_KEY] = True
        session.permanent = True
        return redirect(url_for("family.home"))

    return render_template(
        "family/unlock.html", error="That password is not right."
    ), 401


@bp.route("/lock", methods=["GET"])
def lock():
    session.pop(SESSION_KEY, None)
    session.pop(ACTIVE_MEMBER_KEY, None)
    return redirect(url_for("main.index"))


@bp.route("/who/<slug>", methods=["POST"])
def set_active_member(slug):
    """The home-screen member toggle -- sets who UI writes are attributed to."""
    if slug in _MEMBER_SLUGS:
        session[ACTIVE_MEMBER_KEY] = slug
    return redirect(request.referrer or url_for("family.home"))


# --------------------------------------------------------------------------
# the four screens (behind the gate). Thin for now.
# --------------------------------------------------------------------------


def _greeting_word(hour: int) -> str:
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


@bp.route("/home", methods=["GET"])
def home():
    now = datetime.now()
    return render_template(
        "family/home.html",
        tab="home",
        active_member=_active_member_slug(),
        greeting_word=_greeting_word(now.hour),
        today_label=f"{now.strftime('%A, %B')} {now.day}",  # %-d isn't portable
    )


def _parse_month(raw: str | None) -> date:
    today = date.today()
    if raw:
        try:
            y, m = raw.split("-")
            return date(int(y), int(m), 1)
        except (ValueError, TypeError):
            pass
    return date(today.year, today.month, 1)


def _month_weeks(first_of_month: date):
    """A list of weeks; each week is 7 dates (Mon..Sun), padded into the
    neighbouring months so the grid is always full rectangles."""
    y, m = first_of_month.year, first_of_month.month
    days_in = _cal.monthrange(y, m)[1]
    start = first_of_month - timedelta(days=first_of_month.weekday())  # back to Monday
    last = date(y, m, days_in)
    end = last + timedelta(days=(6 - last.weekday()))                  # fwd to Sunday
    weeks, cur = [], start
    while cur <= end:
        weeks.append([cur + timedelta(days=i) for i in range(7)])
        cur += timedelta(days=7)
    return weeks


@bp.route("/calendar", methods=["GET"])
def calendar():
    first = _parse_month(request.args.get("month"))
    y, m = first.year, first.month
    days_in = _cal.monthrange(y, m)[1]
    grid_events = cal_svc.events_in_range(first, date(y, m, days_in))
    by_day: dict[date, list] = {}
    for d, ev in grid_events:
        by_day.setdefault(d, []).append(ev)
    prev_m = (first - timedelta(days=1)).replace(day=1)
    next_m = (date(y, m, days_in) + timedelta(days=1))
    return render_template(
        "family/calendar.html",
        tab="calendar",
        month_first=first,
        month_label=first.strftime("%B %Y"),
        weeks=_month_weeks(first),
        by_day=by_day,
        today=date.today(),
        prev_month=prev_m.strftime("%Y-%m"),
        next_month=next_m.strftime("%Y-%m"),
        upcoming=cal_svc.upcoming(10),
        weekday_initials=["M", "T", "W", "T", "F", "S", "S"],
    )


@bp.route("/calendar/events", methods=["POST"])
def calendar_add():
    f = request.form
    repeat = (f.get("repeat") or "none").strip()
    data = {
        "title": f.get("title"),
        "starts_on": f.get("starts_on"),
        "all_day": f.get("all_day") == "on",
        "starts_at": f.get("starts_at") or None,
        "ends_at": f.get("ends_at") or None,
        "note": f.get("note"),
    }
    if repeat in ("weekly", "monthly"):
        data["recurrence"] = {
            "freq": repeat,
            "interval": f.get("interval") or 1,
            "byday": f.getlist("byday"),
            "until": f.get("repeat_until") or None,
        }
    try:
        cal_svc.create_event(data, member=_active_member())
    except FamilyError as exc:
        abort(400, str(exc))
    return redirect(url_for("family.calendar", month=f.get("month") or None))


@bp.route("/calendar/events/<int:event_id>/delete", methods=["POST"])
def calendar_delete(event_id):
    try:
        cal_svc.delete_event(event_id)
    except FamilyError:
        abort(404)
    return redirect(url_for("family.calendar"))


@bp.route("/grocery", methods=["GET"])
def grocery():
    order = groc_svc.current_order(member=_active_member())
    items = order.items.all()
    return render_template(
        "family/grocery.html",
        tab="grocery",
        order=order,
        items_todo=[i for i in items if not i.got],
        items_got=[i for i in items if i.got],
        history=groc_svc.order_history(20),
    )


@bp.route("/grocery/items", methods=["POST"])
def grocery_add_item():
    f = request.form
    try:
        groc_svc.add_item(
            {"name": f.get("name"), "quantity": f.get("quantity"), "note": f.get("note")},
            member=_active_member(),
        )
    except FamilyError as exc:
        abort(400, str(exc))
    return redirect(url_for("family.grocery"))


@bp.route("/grocery/items/<int:item_id>/toggle", methods=["POST"])
def grocery_toggle_item(item_id):
    try:
        item = groc_svc.toggle_item(item_id)
    except FamilyError:
        abort(404)
    if request.headers.get("X-Requested-With") == "fetch":
        return {"id": item.id, "got": item.got}
    return redirect(url_for("family.grocery"))


@bp.route("/grocery/items/<int:item_id>/delete", methods=["POST"])
def grocery_delete_item(item_id):
    try:
        groc_svc.remove_item(item_id)
    except FamilyError:
        abort(404)
    return redirect(url_for("family.grocery"))


@bp.route("/grocery/order/new", methods=["POST"])
def grocery_new_order():
    groc_svc.start_new_order(member=_active_member())
    return redirect(url_for("family.grocery"))


@bp.route("/grocery/order/archive", methods=["POST"])
def grocery_archive_order():
    groc_svc.archive_current(member=_active_member())
    return redirect(url_for("family.grocery"))


@bp.route("/grocery/order/copy/<int:from_id>", methods=["POST"])
def grocery_copy_order(from_id):
    try:
        groc_svc.copy_order_forward(from_id, member=_active_member())
    except FamilyError:
        abort(404)
    return redirect(url_for("family.grocery"))


@bp.route("/chat", methods=["GET"])
def chat():
    slug = request.args.get("member")
    if slug not in _MEMBER_SLUGS:
        slug = _active_member_slug()
    member = FamilyMember.by_slug(slug) or _active_member()
    thread = (
        FamilyChatMessage.query.filter_by(member_id=member.id)
        .filter(FamilyChatMessage.role.in_(("user", "assistant")))
        .order_by(FamilyChatMessage.created_at.asc(), FamilyChatMessage.id.asc())
        .all()
    )
    members = FamilyMember.query.order_by(FamilyMember.slug).all()
    return render_template(
        "family/chat.html",
        tab="chat",
        member=member,
        members=members,
        thread=thread,
    )


@bp.route("/api/chat", methods=["POST"])
@limiter.limit(lambda: current_app.config["FAMILY_CHAT_RATE_LIMIT"])
def api_chat():
    data = request.get_json(silent=True) or {}
    slug = data.get("member_id") or data.get("member")
    if slug not in _MEMBER_SLUGS:
        return jsonify({"reply": "I'm not sure who's asking.", "error": True}), 400
    member = FamilyMember.by_slug(slug)
    if member is None:
        return jsonify({"reply": "I'm not sure who's asking.", "error": True}), 400

    try:
        result = chat_svc.answer(member, data.get("message"), config=current_app.config)
    except chat_svc.FamilyChatInputError as exc:
        return jsonify({"reply": str(exc), "error": True}), 400
    except chat_svc.FamilyChatUnavailable:
        return jsonify({"reply": _OFFLINE_MSG, "error": True}), 503
    except Exception:  # noqa: BLE001 - never a trace to the browser
        current_app.logger.exception("family chat failed")
        return jsonify({"reply": _OFFLINE_MSG, "error": True}), 503

    return jsonify({"reply": result.reply, "error": False})

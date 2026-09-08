"""Nelson's private job-application tracker at /job-tracker.

Gated exactly like /documentation (see that blueprint): a signed-session
flag set by a correct password POST, nothing rendered to an
unauthenticated request, and **fails closed** -- if JOB_TRACKER_PASSWORD_HASH
isn't configured there is no password that opens the section.

Deliberately hidden on top of the gate: its own path outside /projects,
never linked from the nav/footer/projects index, `noindex` on every
response (meta tag *and* X-Robots-Tag header), and listed in robots.txt's
Disallow. The gate is the access control; the hiding just keeps the URL
and the unlock form out of search results.

Unlike the older public-write blueprints, this one is NOT csrf-exempt --
every mutating form carries a token.
"""
from urllib.parse import urlparse

from flask import (
    abort,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from app.blueprints.job_tracker import bp
from app.extensions import limiter
from app.services import job_tracker

SESSION_KEY = "job_tracker_unlocked"

# Endpoints reachable without unlocking: the gate itself. Everything else
# on the blueprint goes through _require_unlocked().
_PUBLIC_ENDPOINTS = {"job_tracker.gate", "job_tracker.unlock"}


def _is_unlocked() -> bool:
    return session.get(SESSION_KEY) is True


def _password_hash() -> str | None:
    return current_app.config.get("JOB_TRACKER_PASSWORD_HASH")


@bp.after_request
def _noindex(response):
    # The section is server-side gated, so a crawler can't read it anyway
    # -- this just makes sure the URL and unlock form never show up in
    # results even if the link leaks somewhere.
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@bp.before_request
def _require_unlocked():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return None
    if not _password_hash():
        # Fail closed: unconfigured means nobody gets in, not everybody.
        return render_template("job_tracker/unlock.html", unavailable=True), 503
    if not _is_unlocked():
        return redirect(url_for("job_tracker.gate"))
    return None


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------


@bp.route("", methods=["GET"])
def gate():
    if not _password_hash():
        return render_template("job_tracker/unlock.html", unavailable=True), 503
    if _is_unlocked():
        return redirect(url_for("job_tracker.dashboard"))
    return render_template("job_tracker/unlock.html")


@bp.route("/unlock", methods=["POST"])
@limiter.limit(
    lambda: current_app.config["JOB_TRACKER_UNLOCK_RATE_LIMIT"],
    # Only a failed attempt costs budget -- a correct password redirects
    # (302) and shouldn't count against the owner.
    deduct_when=lambda response: response.status_code != 302,
)
def unlock():
    password_hash = _password_hash()
    if not password_hash:
        return render_template("job_tracker/unlock.html", unavailable=True), 503

    submitted = request.form.get("password") or ""
    # Constant-time compare: a wrong guess can't be narrowed by timing.
    if check_password_hash(password_hash, submitted):
        session[SESSION_KEY] = True
        session.permanent = True
        return redirect(url_for("job_tracker.dashboard"))

    return render_template(
        "job_tracker/unlock.html", error="That password is not right."
    ), 401


@bp.route("/lock", methods=["GET"])
def lock():
    session.pop(SESSION_KEY, None)
    return redirect(url_for("main.index"))


# --------------------------------------------------------------------------
# the tracker (everything below is behind the gate via _require_unlocked)
# --------------------------------------------------------------------------


@bp.route("/board", methods=["GET"])
def dashboard():
    status_filter = request.args.get("status") or None
    search = request.args.get("q") or None
    sort = request.args.get("sort") or "-date_applied"
    if status_filter and status_filter not in job_tracker.STATUSES:
        status_filter = None

    applications = job_tracker.list_applications(
        status=status_filter, search=search, sort=sort
    )
    return render_template(
        "job_tracker/dashboard.html",
        applications=applications,
        stats=job_tracker.summary_stats(),
        status_counts=job_tracker.status_counts(),
        statuses=job_tracker.STATUSES,
        match_label=job_tracker.match_label,
        active_status=status_filter,
        search=search or "",
        sort=sort,
    )


@bp.route("/new", methods=["GET", "POST"])
def new_application():
    if request.method == "POST":
        try:
            app = job_tracker.create_application(_form_data(request.form), source="web")
        except job_tracker.JobTrackerError as exc:
            return render_template(
                "job_tracker/form.html",
                statuses=job_tracker.STATUSES,
                form=request.form,
                error=str(exc),
                application=None,
            ), 400
        return redirect(url_for("job_tracker.edit_application", app_id=app.id))

    return render_template(
        "job_tracker/form.html",
        statuses=job_tracker.STATUSES,
        form={},
        application=None,
    )


@bp.route("/<int:app_id>", methods=["GET", "POST"])
def edit_application(app_id: int):
    try:
        application = job_tracker.get_application(app_id)
    except job_tracker.JobTrackerError:
        abort(404)

    if request.method == "POST":
        try:
            job_tracker.update_application(app_id, _form_data(request.form), source="web")
        except job_tracker.JobTrackerError as exc:
            return render_template(
                "job_tracker/form.html",
                statuses=job_tracker.STATUSES,
                form=request.form,
                error=str(exc),
                application=application,
            ), 400
        return redirect(url_for("job_tracker.edit_application", app_id=app_id))

    return render_template(
        "job_tracker/form.html",
        statuses=job_tracker.STATUSES,
        form=_application_to_form(application),
        application=application,
        events=application.events.limit(50).all(),
        match_label=job_tracker.match_label,
    )


@bp.route("/<int:app_id>/status", methods=["POST"])
def change_status(app_id: int):
    """The dashboard's inline status <select> -- one field, straight back
    to the board (keeping whatever filter it was viewed under)."""
    new_status = request.form.get("status") or ""
    try:
        job_tracker.set_status(app_id, new_status, source="web")
    except job_tracker.JobTrackerError as exc:
        abort(400, str(exc))
    return redirect(_safe_board_url(request.form.get("next")))


@bp.route("/<int:app_id>/delete", methods=["POST"])
def delete_application(app_id: int):
    try:
        job_tracker.delete_application(app_id, source="web")
    except job_tracker.JobTrackerError:
        abort(404)
    return redirect(url_for("job_tracker.dashboard"))


@bp.route("/audit", methods=["GET"])
def audit():
    return render_template(
        "job_tracker/audit.html", events=job_tracker.recent_events(200)
    )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

_TEXT_FIELDS = (
    "company_name",
    "role_title",
    "job_posting_url",
    "date_applied",
    "status",
    "source",
    "resume_version",
    "company_industry",
    "company_size_stage",
    "location_remote_policy",
    "tech_stack",
    "salary_range",
    "match_grade",
    "match_notes",
    "notes",
)


def _safe_board_url(target: str | None) -> str:
    """Only honour a `next` that is a same-site, path-only URL under this
    blueprint -- never an absolute or protocol-relative URL, so a status
    change can't be turned into an open redirect via a spoofed form field
    or Referer. Same rule as auth._safe_next."""
    if target and target.startswith("/job-tracker") and not target.startswith("//"):
        parsed = urlparse(target)
        if not parsed.scheme and not parsed.netloc:
            return target
    return url_for("job_tracker.dashboard")


def _form_data(form) -> dict:
    """Pull only the fields the service knows about off a submitted form.
    A checkbox that's unticked isn't in the form at all, so
    cover_letter_used is derived from presence."""
    data = {field: form.get(field, "") for field in _TEXT_FIELDS if field in form}
    data["cover_letter_used"] = bool(form.get("cover_letter_used"))
    return data


def _application_to_form(app) -> dict:
    return {
        "company_name": app.company_name or "",
        "role_title": app.role_title or "",
        "job_posting_url": app.job_posting_url or "",
        "date_applied": app.date_applied.isoformat() if app.date_applied else "",
        "status": app.status or "Applied",
        "source": app.source or "",
        "resume_version": app.resume_version or "",
        "cover_letter_used": app.cover_letter_used,
        "company_industry": app.company_industry or "",
        "company_size_stage": app.company_size_stage or "",
        "location_remote_policy": app.location_remote_policy or "",
        "tech_stack": ", ".join(app.tech_stack_list),
        "salary_range": app.salary_range or "",
        "match_grade": "" if app.match_grade is None else str(app.match_grade),
        "match_notes": app.match_notes or "",
        "notes": app.notes or "",
    }

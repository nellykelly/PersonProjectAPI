from flask import current_app, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from app.blueprints.documentation import bp
from app.extensions import limiter

# Session key set once a visitor has entered the right password. Gates the
# whole /documentation namespace now (previously just /interview) -- the
# section isn't linked from the site nav anymore, only from the Contact
# page, and the point of moving it behind a password is that an
# unauthenticated request never receives ANY of it, index included. The
# session cookie is signed with SECRET_KEY (and Secure + SameSite=Lax in
# production, see config.py), so this can't be forged client-side the way
# a plain "unlocked=true" localStorage flag could.
SESSION_KEY = "docs_unlocked"


def _is_unlocked() -> bool:
    return session.get(SESSION_KEY) is True


@bp.route("", methods=["GET", "POST"])
# Rate limited on the POST specifically because this is the one endpoint on
# the site where guessing repeatedly is the whole attack. Applied per IP by
# the shared limiter, backed by Redis in production so the count survives a
# restart rather than resetting the attacker's budget.
@limiter.limit(
    lambda: current_app.config["DOCS_UNLOCK_RATE_LIMIT"],
    methods=["POST"],
    deduct_when=lambda response: response.status_code != 302,
)
def index():
    """The engineering reference for this codebase, password-gated.

    Gated server-side rather than hidden client-side: the point is that
    an unauthenticated request never receives the content at all. A
    JavaScript show/hide, or a template that renders the reference and
    then covers it, still ships everything in the HTML to anyone who
    opens view-source.

    Fails **closed**. If DOCS_PASSWORD_HASH isn't configured on this
    deployment there is no password that can open the section, rather
    than it defaulting to open or to some checked-in fallback value.

    Deliberately does NOT extend base.html when unlocked: it carries its
    own typography, palette and sticky index, because it's a long-form
    reference document rather than another page of the site, and the
    site's own chrome would fight its reading column.
    """
    password_hash = current_app.config.get("DOCS_PASSWORD_HASH")

    if _is_unlocked():
        return render_template("documentation/index.html")

    if not password_hash:
        return render_template("documentation/unlock.html", unavailable=True), 503

    if request.method == "POST":
        submitted = request.form.get("password") or ""
        # check_password_hash compares in constant time, so a wrong guess
        # can't be narrowed down by timing how long the response took.
        if check_password_hash(password_hash, submitted):
            session[SESSION_KEY] = True
            session.permanent = True
            # Redirect rather than rendering here, so a refresh doesn't
            # re-POST the password and the URL is a normal GET.
            return redirect(url_for("documentation.index"))
        return render_template(
            "documentation/unlock.html",
            error="That password is not right.",
        ), 401

    return render_template("documentation/unlock.html")


@bp.route("/interview")
def interview():
    """The interview question bank, one level deeper behind the same gate.

    No separate password: reaching this page at all already required
    unlocking /documentation, so this just re-checks the same session key
    and funnels a direct/guessed URL back through the one gate rather than
    trusting the referrer.
    """
    if not _is_unlocked():
        return redirect(url_for("documentation.index"))
    return render_template("documentation/interview.html")


@bp.route("/lock")
def logout():
    session.pop(SESSION_KEY, None)
    return redirect(url_for("main.index"))

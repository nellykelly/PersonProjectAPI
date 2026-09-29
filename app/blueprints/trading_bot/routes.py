from flask import current_app, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from app.blueprints.trading_bot import bp
from app.extensions import limiter
from app.services import trading_bot

# Gated exactly like /documentation (see that blueprint's docstring): a
# signed-session flag set by a correct password POST, nothing rendered to
# an unauthenticated request, and **fails closed** -- if
# TRADING_BOT_PASSWORD_HASH isn't configured there is no password that
# opens the section. A real (paper-for-now, possibly real-money-later)
# brokerage account is the highest-sensitivity data on this site, so this
# gets the same treatment as the docs/job-tracker/family sections: its own
# password, not linked from nav/footer/projects index, noindex on every
# response, and listed in robots.txt's Disallow.
SESSION_KEY = "trading_bot_unlocked"


def _is_unlocked() -> bool:
    return session.get(SESSION_KEY) is True


def _dashboard():
    return trading_bot.get_dashboard_data(
        current_app.config["TRADING_BOT_STATS_PATH"],
        current_app.config["TRADING_BOT_STALE_AFTER_HOURS"],
    )


@bp.after_request
def _noindex(response):
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@bp.route("", methods=["GET", "POST"])
# Rate limited on the POST specifically -- repeated guessing is the whole
# attack against this endpoint. Only a failed attempt costs budget (a
# correct password redirects, 302).
@limiter.limit(
    lambda: current_app.config["TRADING_BOT_UNLOCK_RATE_LIMIT"],
    methods=["POST"],
    deduct_when=lambda response: response.status_code != 302,
)
def index():
    password_hash = current_app.config.get("TRADING_BOT_PASSWORD_HASH")

    if _is_unlocked():
        return render_template("trading_bot/index.html", data=_dashboard())

    if not password_hash:
        return render_template("trading_bot/unlock.html", unavailable=True), 503

    if request.method == "POST":
        submitted = request.form.get("password") or ""
        # Constant-time compare: a wrong guess can't be narrowed by timing.
        if check_password_hash(password_hash, submitted):
            session[SESSION_KEY] = True
            session.permanent = True
            return redirect(url_for("trading_bot.index"))
        return render_template(
            "trading_bot/unlock.html", error="That password is not right."
        ), 401

    return render_template("trading_bot/unlock.html")


@bp.route("/research")
def research():
    """One level deeper behind the same gate -- no separate password,
    since reaching this URL at all already required unlocking the
    overview. Re-checks the same session key rather than trusting the
    referrer, so a direct/guessed URL funnels back through the one gate."""
    if not _is_unlocked():
        return redirect(url_for("trading_bot.index"))
    return render_template("trading_bot/research.html", data=_dashboard())


@bp.route("/lock")
def lock():
    session.pop(SESSION_KEY, None)
    return redirect(url_for("main.index"))

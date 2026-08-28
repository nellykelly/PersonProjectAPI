from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from app.blueprints.auth import bp
from app.blueprints.auth.forms import LoginForm, RegisterForm
from app.extensions import db, limiter
from app.models import User


def _safe_next(target: str | None) -> str | None:
    """Only honour a `?next=` that is a same-site, path-only URL -- never
    an absolute URL or protocol-relative `//host`, so login can't be used
    as an open redirect."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    return target


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit(
    lambda: current_app.config["AUTH_LOGIN_RATE_LIMIT"],
    methods=["POST"],
    # Only failed attempts burn the budget -- a successful login (302)
    # shouldn't count against someone who just signed in normally.
    deduct_when=lambda response: response.status_code != 302,
)
def login():
    next_url = _safe_next(request.args.get("next"))
    if current_user.is_authenticated:
        return redirect(next_url or url_for("leetcode.index"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(
            username_ci=User.normalize_username(form.username.data)
        ).first()
        # One generic message for "no such user" and "wrong password" so
        # the form can't be used to enumerate which usernames exist.
        if user is None or not user.check_password(form.password.data):
            flash("Wrong username or password.", "error")
            return render_template("auth/login.html", form=form, next=next_url), 401
        login_user(user, remember=form.remember.data)
        user.last_login_at = datetime.now(timezone.utc)
        db.session.commit()
        return redirect(next_url or url_for("leetcode.index"))

    return render_template("auth/login.html", form=form, next=next_url)


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit(
    lambda: current_app.config["AUTH_REGISTER_RATE_LIMIT"],
    methods=["POST"],
    deduct_when=lambda response: response.status_code != 302,
)
def register():
    if not current_app.config.get("REGISTRATION_ENABLED", True):
        return render_template("auth/register.html", form=None, closed=True), 403
    if current_user.is_authenticated:
        return redirect(url_for("leetcode.index"))

    form = RegisterForm()
    if form.validate_on_submit():
        user = User()
        user.set_username(form.username.data)
        user.set_password(form.password.data)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            # Lost a race for the same username between validate_username
            # and commit.
            db.session.rollback()
            form.username.errors.append("That username is taken.")
            return render_template("auth/register.html", form=form), 409
        login_user(user)
        user.last_login_at = datetime.now(timezone.utc)
        db.session.commit()
        flash("Account created -- your board is ready.", "success")
        return redirect(url_for("leetcode.index"))

    return render_template("auth/register.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Logged out.", "success")
    return redirect(url_for("main.index"))

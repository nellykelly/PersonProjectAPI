"""Private client pages at /clients/<slug> -- handover docs and previews
shown to freelance clients.

Gated like /documentation and /family: a signed-session flag set by a
correct password POST, nothing rendered to a locked request, and **fails
closed** -- if a client's password hash isn't configured, there is no
password that opens that page. Each client has its own hash (one client's
password never opens another's page) and its own session flag.

Standalone templates (never `{% extends "base.html" %}`) so a client sees
their project, not the portfolio chrome. `noindex` on every response
(meta tag *and* X-Robots-Tag header) plus a robots.txt Disallow. Not
csrf-exempt.
"""
from flask import abort, current_app, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from app.blueprints.clients import bp
from app.extensions import limiter

# slug -> (display name, config key holding its Werkzeug hash, template)
CLIENTS = {
    "espressocaribbean": (
        "Espresso Caribbean",
        "CLIENT_ESPRESSOCARIBBEAN_PASSWORD_HASH",
        "clients/espressocaribbean.html",
    ),
}


def _session_key(slug: str) -> str:
    return f"client_{slug}_unlocked"


@bp.after_request
def _noindex(response):
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    # The unlocked page is private; don't let a shared cache keep it.
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/<slug>", methods=["GET", "POST"])
@limiter.limit(
    lambda: current_app.config["CLIENTS_UNLOCK_RATE_LIMIT"],
    methods=["POST"],
    deduct_when=lambda response: response.status_code != 302,
)
def page(slug):
    if slug not in CLIENTS:
        abort(404)
    name, hash_key, template = CLIENTS[slug]
    password_hash = current_app.config.get(hash_key)
    if not password_hash:
        # Fail closed: unconfigured means nobody gets in.
        return render_template("clients/gate.html", client=name, unavailable=True), 503

    if session.get(_session_key(slug)) is True:
        return render_template(template, client=name, slug=slug)

    if request.method == "POST":
        # check_password_hash compares in constant time.
        if check_password_hash(password_hash, request.form.get("password") or ""):
            session[_session_key(slug)] = True
            session.permanent = True
            # Post/redirect/get: a refresh doesn't re-POST the password.
            return redirect(url_for("clients.page", slug=slug))
        return render_template(
            "clients/gate.html", client=name, slug=slug, error="That password is not right."
        ), 401

    return render_template("clients/gate.html", client=name, slug=slug)


@bp.route("/<slug>/lock", methods=["GET"])
def lock(slug):
    if slug not in CLIENTS:
        abort(404)
    session.pop(_session_key(slug), None)
    return redirect(url_for("clients.page", slug=slug))

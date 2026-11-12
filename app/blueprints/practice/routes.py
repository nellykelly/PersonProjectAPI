"""/practice -- the ATL-101 dbt + SQL practice sandbox, behind a password.

  /practice/        the gate (GET shows it, POST checks the password)
  /practice/app/... the sandbox UI and its /api/*, proxied to the `sandbox` service
  /practice/lock    forget the unlock

Gated like /clients and /family: a signed-session flag set by a correct password POST,
nothing proxied for a locked request, and it **fails closed** -- no PRACTICE_PASSWORD_HASH
means no password opens it. noindex on every response plus a robots.txt Disallow.

The gate form carries a CSRF token. The proxy view is csrf-exempt because the sandbox UI
posts JSON from plain fetch() calls; instead it requires the X-Sandbox header the UI sends
on every call. A cross-site page can't add a custom header without a CORS preflight, which
this app never answers, so that header does the same job as the token.
"""
from flask import (
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from app.blueprints.practice import bp
from app.extensions import csrf, limiter
from app.services import practice_sandbox as sandbox

UNLOCKED = "practice_unlocked"


def _unlocked() -> bool:
    return session.get(UNLOCKED) is True and sandbox.valid_sandbox_id(session.get(sandbox.SESSION_KEY))


@bp.after_request
def _noindex(response):
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers.setdefault("Cache-Control", "private, no-store")
    return response


@bp.route("/", methods=["GET", "POST"])
@limiter.limit(
    lambda: current_app.config["PRACTICE_UNLOCK_RATE_LIMIT"],
    methods=["POST"],
    deduct_when=lambda response: response.status_code != 302,
)
def gate():
    password_hash = current_app.config.get("PRACTICE_PASSWORD_HASH")
    if not password_hash:
        return render_template("practice/gate.html", unavailable=True), 503
    if _unlocked():
        return redirect(url_for("practice.app", path=""))
    if request.method == "POST":
        # check_password_hash compares in constant time.
        if check_password_hash(password_hash, request.form.get("password") or ""):
            session[UNLOCKED] = True
            # Keep an existing sandbox id so re-unlocking doesn't strand your work.
            if not sandbox.valid_sandbox_id(session.get(sandbox.SESSION_KEY)):
                session[sandbox.SESSION_KEY] = sandbox.new_sandbox_id()
            session.permanent = True
            return redirect(url_for("practice.app", path=""))
        return render_template("practice/gate.html", error="That password is not right."), 401
    return render_template("practice/gate.html")


@bp.route("/app", methods=["GET"])
def app_root():
    return redirect(url_for("practice.app", path=""))


@bp.route("/app/", defaults={"path": ""}, methods=["GET", "POST"])
@bp.route("/app/<path:path>", methods=["GET", "POST"])
@csrf.exempt
@limiter.limit(lambda: current_app.config["PRACTICE_PROXY_RATE_LIMIT"])
def app(path):
    is_api = path.startswith("api/")
    if not current_app.config.get("PRACTICE_PASSWORD_HASH") or not _unlocked():
        if is_api or request.method == "POST":
            return jsonify(ok=False, error="Locked: open /practice/ and enter the password again."), 401
        return redirect(url_for("practice.gate"))
    if request.method == "POST":
        if request.headers.get("X-Sandbox") != "1":
            return jsonify(ok=False, error="Missing X-Sandbox header."), 403
        if (request.content_length or 0) > sandbox.MAX_BODY:
            return jsonify(ok=False, error="Request too large."), 413
    try:
        status, headers, content = sandbox.forward(
            current_app.config["PRACTICE_SANDBOX_URL"],
            request.method,
            path or "index.html",
            request.args,
            request.headers,
            request.get_data() if request.method == "POST" else None,
            session[sandbox.SESSION_KEY],
            current_app.config["PRACTICE_PROXY_TIMEOUT"],
        )
    except sandbox.SandboxUnavailable:
        current_app.logger.exception("practice sandbox unreachable")
        if is_api:
            return jsonify(ok=False, error="The sandbox service isn't reachable right now."), 503
        return render_template("practice/gate.html", offline=True), 503
    response = Response(content, status=status, headers=headers)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/lock", methods=["GET"])
def lock():
    # Forget the unlock but keep the sandbox id, so unlocking again finds your work.
    session.pop(UNLOCKED, None)
    return redirect(url_for("practice.gate"))

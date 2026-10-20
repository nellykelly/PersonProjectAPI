"""Private client sites at /clients/<slug>/ -- full copies of client
websites, shown to the client and the people they share it with.

Each site is a static export (built by scripts/build_client_<slug>.sh into
app/client_sites/<slug>/) served through Flask rather than Caddy, because
every file -- pages *and* assets -- sits behind the password gate. That
costs a gunicorn thread per asset request, which is fine at preview
traffic levels (see GOTCHAS.md on the thread budget).

Gated like /documentation and /family: a signed-session flag set by a
correct password POST, nothing served to a locked request, and **fails
closed** -- if a client's password hash isn't configured, there is no
password that opens that site. Each client has its own hash and its own
session flag. `noindex` on every response plus a robots.txt Disallow.
Not csrf-exempt.
"""
from pathlib import Path

from flask import (
    abort,
    current_app,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from app.blueprints.clients import bp
from app.extensions import limiter

SITES_DIR = Path(__file__).resolve().parents[2] / "client_sites"

# slug -> (display name, config key holding its Werkzeug hash)
CLIENTS = {
    "espressocaribbean": ("Espresso Caribbean", "CLIENT_ESPRESSOCARIBBEAN_PASSWORD_HASH"),
}


def _session_key(slug: str) -> str:
    return f"client_{slug}_unlocked"


def _client(slug: str) -> tuple[str, str | None]:
    if slug not in CLIENTS:
        abort(404)
    name, hash_key = CLIENTS[slug]
    return name, current_app.config.get(hash_key)


def _resolve(site: Path, path: str) -> str | None:
    """Map a URL path onto a file in the export (trailingSlash layout:
    `recipes/` -> `recipes/index.html`)."""
    if path == "" or path.endswith("/"):
        candidates = [path + "index.html"]
    else:
        candidates = [path, path + "/index.html", path + ".html"]
    for rel in candidates:
        target = (site / rel).resolve()
        if target.is_file() and site in target.parents:
            return rel
    return None


@bp.after_request
def _noindex(response):
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers.setdefault("Cache-Control", "private, no-store")
    return response


@bp.route("/<slug>", methods=["GET"])
def root(slug):
    _client(slug)
    return redirect(url_for("clients.site", slug=slug, path=""))


@bp.route("/<slug>/", defaults={"path": ""}, methods=["GET", "POST"])
@bp.route("/<slug>/<path:path>", methods=["GET"])
@limiter.limit(
    lambda: current_app.config["CLIENTS_UNLOCK_RATE_LIMIT"],
    methods=["POST"],
    deduct_when=lambda response: response.status_code != 302,
)
def site(slug, path):
    name, password_hash = _client(slug)
    if not password_hash:
        # Fail closed: unconfigured means nobody gets in.
        return render_template("clients/gate.html", client=name, unavailable=True), 503

    if session.get(_session_key(slug)) is not True:
        if request.method == "POST":
            # check_password_hash compares in constant time.
            if check_password_hash(password_hash, request.form.get("password") or ""):
                session[_session_key(slug)] = True
                session.permanent = True
                # Post/redirect/get: a refresh doesn't re-POST the password.
                return redirect(url_for("clients.site", slug=slug, path=""))
            return render_template(
                "clients/gate.html", client=name, slug=slug, error="That password is not right."
            ), 401
        if path:
            # Deep link while locked: unlock first, land on the home page.
            return redirect(url_for("clients.site", slug=slug, path=""))
        return render_template("clients/gate.html", client=name, slug=slug)

    if request.method == "POST":
        return redirect(url_for("clients.site", slug=slug, path=""))

    site_dir = SITES_DIR / slug
    rel = _resolve(site_dir, path)
    if rel is None:
        if (site_dir / "404.html").is_file():
            return send_from_directory(site_dir, "404.html", max_age=0), 404
        abort(404)

    mimetype = "text/javascript" if rel.endswith(".mjs") else None
    response = send_from_directory(site_dir, rel, mimetype=mimetype)
    if rel.startswith("_next/static/"):
        # Content-hashed filenames: safe to cache for good (in this browser only).
        response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    elif rel.endswith(".html"):
        response.headers["Cache-Control"] = "private, no-store"
    else:
        response.headers["Cache-Control"] = "private, no-cache"
    return response


@bp.route("/<slug>/lock", methods=["GET"])
def lock(slug):
    _client(slug)
    session.pop(_session_key(slug), None)
    return redirect(url_for("clients.site", slug=slug, path=""))

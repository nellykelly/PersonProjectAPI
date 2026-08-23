from flask import Response, render_template, send_from_directory, current_app

from app.blueprints.main import bp
from app.blueprints.projects.routes import PROJECTS

# The 3 most substantial builds, for the landing page's highlight -- the
# full 5-project set (plus Earlier Projects) already lives at /projects,
# this is just a taste of it.
FEATURED_PROJECT_SLUGS = ("trading-simulator", "pipeline-world", "qr-quant-scraper")


@bp.route("/")
def index():
    featured = [p for p in PROJECTS if p["slug"] in FEATURED_PROJECT_SLUGS]
    return render_template("main/index.html", featured_projects=featured)


@bp.route("/favicon.ico")
def favicon():
    # Browsers request this at the domain root regardless of the <link
    # rel="icon"> tag in base.html -- served here so it isn't a 404 in
    # every server log / browser console.
    return send_from_directory(
        f"{current_app.static_folder}/assets/img", "favicon.ico", mimetype="image/vnd.microsoft.icon"
    )


@bp.route("/robots.txt")
def robots():
    # Search engines request this at the domain root, not under /static/
    # -- same reason favicon.ico gets its own route above rather than
    # relying on url_for('static', ...). Fully permissive: nothing on
    # this site is private or admin-only, and the entire point of a
    # portfolio site is to be found.
    return Response("User-agent: *\nAllow: /\n", mimetype="text/plain")

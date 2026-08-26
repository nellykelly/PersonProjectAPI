from flask import Response, render_template, send_from_directory, current_app

from app.blueprints.main import bp
from app.blueprints.projects.routes import listed_projects

# The most substantial builds, for the landing page's highlight -- the
# full set (plus Earlier Projects) already lives at /projects, this is
# just a taste of it. Filtered through listed_projects(), so a project
# put on hold drops off the landing page too rather than needing to be
# removed from this tuple as well and being missed.
FEATURED_PROJECT_SLUGS = ("pipeline-world", "qr-quant-scraper", "timed-squares")


@bp.route("/")
def index():
    listed = listed_projects()
    featured = [p for p in listed if p["slug"] in FEATURED_PROJECT_SLUGS]
    # Counted, not written into the template: it drifted before (the copy
    # still said 5 after a sixth shipped), and putting a project on hold
    # changes it again.
    return render_template("main/index.html", featured_projects=featured, project_count=len(listed))


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
    # relying on url_for('static', ...). Permissive everywhere except the
    # password-gated documentation section (also unlinked from the nav,
    # reachable only via /contact): a crawler can't read it anyway (the
    # gate is server-side), but there's no reason to advertise the URL or
    # have the unlock form show up in results. This is a hint to
    # well-behaved crawlers, not the access control -- that's the gate.
    return Response(
        "User-agent: *\nAllow: /\nDisallow: /documentation\n",
        mimetype="text/plain",
    )

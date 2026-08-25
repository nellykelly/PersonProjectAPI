from flask import Response, render_template, send_from_directory, current_app

from app.blueprints.main import bp
from app.blueprints.projects.routes import listed_projects


@bp.route("/")
def index():
    # The home page renders every listed project as a node in the site's
    # real topology, not a curated highlight subset -- putting a project
    # on hold drops it here automatically via listed_projects().
    listed = listed_projects()
    return render_template("main/index.html", projects=listed, project_count=len(listed))


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
    # password-gated interview section: a crawler can't read it anyway
    # (the gate is server-side), but there's no reason to advertise the
    # URL or have the unlock form show up in results. This is a hint to
    # well-behaved crawlers, not the access control -- that's the gate.
    return Response(
        "User-agent: *\nAllow: /\nDisallow: /documentation/interview\n",
        mimetype="text/plain",
    )

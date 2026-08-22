from flask import render_template, send_from_directory, current_app

from app.blueprints.main import bp


@bp.route("/")
def index():
    return render_template("main/index.html")


@bp.route("/favicon.ico")
def favicon():
    # Browsers request this at the domain root regardless of the <link
    # rel="icon"> tag in base.html -- served here so it isn't a 404 in
    # every server log / browser console.
    return send_from_directory(
        f"{current_app.static_folder}/assets/img", "favicon.ico", mimetype="image/vnd.microsoft.icon"
    )

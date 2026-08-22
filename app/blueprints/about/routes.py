from flask import render_template

from app.blueprints.about import bp


@bp.route("")
def index():
    return render_template("about/index.html")

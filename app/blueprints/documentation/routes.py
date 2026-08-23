from flask import render_template

from app.blueprints.documentation import bp


@bp.route("")
def index():
    """The engineering reference for this codebase.

    Deliberately does NOT extend base.html: it carries its own
    typography, palette and sticky index, because it's a long-form
    reference document rather than another page of the site, and the
    site's own chrome would fight its reading column. The nav link back
    to the rest of the site is baked into the template instead.
    """
    return render_template("documentation/index.html")

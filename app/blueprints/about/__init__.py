from flask import Blueprint

bp = Blueprint("about", __name__)

from app.blueprints.about import routes  # noqa: E402,F401

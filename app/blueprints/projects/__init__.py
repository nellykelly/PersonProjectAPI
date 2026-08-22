from flask import Blueprint

bp = Blueprint("projects", __name__)

from app.blueprints.projects import routes  # noqa: E402,F401

from flask import Blueprint

bp = Blueprint("timed_squares", __name__)

from app.blueprints.timed_squares import routes  # noqa: E402,F401

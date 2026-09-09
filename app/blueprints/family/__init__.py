from flask import Blueprint

bp = Blueprint("family", __name__)

from app.blueprints.family import routes  # noqa: E402,F401

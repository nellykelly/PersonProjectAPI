from flask import Blueprint

bp = Blueprint("practice", __name__)

from app.blueprints.practice import routes  # noqa: E402,F401

from flask import Blueprint

bp = Blueprint("documentation", __name__)

from app.blueprints.documentation import routes  # noqa: E402,F401

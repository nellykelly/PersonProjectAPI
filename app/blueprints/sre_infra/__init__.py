from flask import Blueprint

bp = Blueprint("sre_infra", __name__)

from app.blueprints.sre_infra import routes  # noqa: E402,F401

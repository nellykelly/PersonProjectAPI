from flask import Blueprint

bp = Blueprint("assistant", __name__)

from app.blueprints.assistant import routes  # noqa: E402,F401

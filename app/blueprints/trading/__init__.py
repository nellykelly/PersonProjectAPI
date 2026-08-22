from flask import Blueprint

bp = Blueprint("trading", __name__)

from app.blueprints.trading import routes  # noqa: E402,F401

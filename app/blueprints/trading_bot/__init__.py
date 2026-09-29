from flask import Blueprint

bp = Blueprint("trading_bot", __name__)

from app.blueprints.trading_bot import routes  # noqa: E402,F401

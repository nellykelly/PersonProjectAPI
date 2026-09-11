from flask import Blueprint

bp = Blueprint("market_warehouse", __name__)

from app.blueprints.market_warehouse import routes  # noqa: E402,F401

from flask import Blueprint

bp = Blueprint("sniffer", __name__)

from app.blueprints.sniffer import routes  # noqa: E402,F401

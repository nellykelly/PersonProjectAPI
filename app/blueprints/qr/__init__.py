from flask import Blueprint

bp = Blueprint("qr", __name__)

from app.blueprints.qr import routes  # noqa: E402,F401

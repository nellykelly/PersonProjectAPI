from flask import Blueprint

bp = Blueprint("contact", __name__)

from app.blueprints.contact import routes  # noqa: E402,F401

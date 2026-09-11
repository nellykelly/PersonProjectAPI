from flask import Blueprint

bp = Blueprint("tiny_jvm", __name__)

from app.blueprints.tiny_jvm import routes  # noqa: E402,F401

from flask import Blueprint

bp = Blueprint("pipeline_world", __name__)

from app.blueprints.pipeline_world import routes, socket_handlers  # noqa: E402,F401

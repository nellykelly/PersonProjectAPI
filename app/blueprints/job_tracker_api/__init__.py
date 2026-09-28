from flask import Blueprint

bp = Blueprint("job_tracker_api", __name__)

from app.blueprints.job_tracker_api import routes  # noqa: E402,F401

from flask import Blueprint

bp = Blueprint("job_tracker", __name__)

from app.blueprints.job_tracker import routes  # noqa: E402,F401

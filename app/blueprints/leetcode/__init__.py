from flask import Blueprint

bp = Blueprint("leetcode", __name__)

from app.blueprints.leetcode import routes  # noqa: E402,F401

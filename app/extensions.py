"""Shared Flask extension instances.

Kept in their own module (rather than inside __init__.py) so blueprints
and services can `from app.extensions import db` without importing the
app factory itself and risking a circular import.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_wtf import CSRFProtect

db = SQLAlchemy()
limiter = Limiter(key_func=get_remote_address)
socketio = SocketIO()
migrate = Migrate()

# Accounts for the LeetCode 150 tracker. login_manager.user_loader and the
# login view are wired in create_app.
login_manager = LoginManager()

# CSRF is enforced app-wide but the pre-account public-write blueprints
# (trading, pipeline_world, timed_squares, documentation) are exempted in
# create_app -- they predate this and post without a token. New POSTs
# (auth, the tracker progress API) are protected.
csrf = CSRFProtect()

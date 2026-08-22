"""Shared Flask extension instances.

Kept in their own module (rather than inside __init__.py) so blueprints
and services can `from app.extensions import db` without importing the
app factory itself and risking a circular import.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

db = SQLAlchemy()
limiter = Limiter(key_func=get_remote_address)

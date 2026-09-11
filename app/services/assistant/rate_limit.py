"""Shared rate-limit bucket for assistant tools and their equivalent web routes.

Hera's tool-calling surface (trading, pipeline-world join, company scorer,
timed-squares, ...) performs the same underlying writes/compute the existing
Flask routes already rate-limit. Without sharing a bucket, a visitor could
sidestep a route's limit by asking the assistant to do the same thing
instead. `consume()` hits the same `flask_limiter`-backed storage the app's
route decorators use (via `app.extensions.limiter`), keyed by action name so
a route and a tool for the same action draw from one pool.
"""
from flask import current_app
from limits import parse

from app.extensions import limiter
from flask_limiter.util import get_remote_address

# RateLimitItem objects are immutable and cheap to reuse; cache them per
# limit string so repeated calls don't re-parse "10 per hour" every hit.
_parsed_limits: dict[str, object] = {}


def consume(action: str, limit_string: str) -> bool:
    """Consume one hit against the named action's rate limit. Returns True if
    allowed, False if the caller has exceeded the limit. Shares one bucket
    per (action, caller IP) -- callers should use the SAME action string in
    both a Flask route and any assistant tool that performs the same
    underlying action, so both channels draw from one pool."""
    if not current_app.config.get("RATELIMIT_ENABLED", True):
        # Mirrors flask_limiter's own route decorators: when rate limiting
        # is administratively disabled, a decorated route just doesn't
        # limit. flask_limiter itself skips building _limiter/_storage in
        # this case (see Limiter.init_app's early return), so touching
        # limiter.limiter below would raise AssertionError rather than
        # meaning "always allowed" -- short-circuit before that happens.
        return True

    item = _parsed_limits.get(limit_string)
    if item is None:
        item = parse(limit_string)
        _parsed_limits[limit_string] = item

    key = f"{action}:{get_remote_address()}"
    return limiter.limiter.hit(item, key)

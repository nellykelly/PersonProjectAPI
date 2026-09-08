"""Who is allowed to make the assistant *do* things (as opposed to just
answer questions).

Two independent gates, both required before Hera is handed the job-tracker
tools:

1. `is_owner()`   -- the request is authenticated (Flask-Login) as the one
   account whose username matches `ADMIN_USERNAME`. Empty/unset
   `ADMIN_USERNAME` means nobody qualifies (fail-closed).
2. the `/job-tracker` password gate is unlocked in this session -- a second
   secret the owner typed deliberately, set by
   `app.blueprints.job_tracker.routes` on a correct password.

`can_use_job_tools()` is the AND of the two. It is the single predicate the
chat route and the orchestrator both consult; when it is false the tool
schemas are never built, so there is nothing for a crafted message or a
retrieved passage to invoke.
"""
from __future__ import annotations

from flask import current_app, session
from flask_login import current_user

# The session flag set by app.blueprints.job_tracker.routes on a correct
# password. Source of truth is `job_tracker.routes.SESSION_KEY`; duplicated
# here as a literal so this module doesn't import a blueprint's private
# helper. Keep the two in sync.
_JOB_TRACKER_SESSION_KEY = "job_tracker_unlocked"


def is_owner() -> bool:
    """True iff the current request is authenticated as the admin account.

    Matched case-insensitively against `User.username_ci`. Returns False
    when `ADMIN_USERNAME` is unset -- an unconfigured deployment grants
    nobody owner powers.
    """
    admin = (current_app.config.get("ADMIN_USERNAME") or "").strip().lower()
    return bool(
        admin
        and current_user.is_authenticated
        and getattr(current_user, "username_ci", None) == admin
    )


def can_use_job_tools() -> bool:
    """True iff the owner is signed in *and* the /job-tracker gate is
    unlocked in this session. The gate for offering Hera any job-tracker
    tool at all."""
    return is_owner() and session.get(_JOB_TRACKER_SESSION_KEY) is True

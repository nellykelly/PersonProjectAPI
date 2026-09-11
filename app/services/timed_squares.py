"""Timed-Squares leaderboard reads -- the query shared by the web route and
the Hera assistant tool, extracted so it lives in exactly one place.

Public and read-only: the leaderboard is visible to every visitor with no
login (see `TimedSquaresScore`'s docstring), so this module has no
authorization concept and no rate limit of its own -- it matches the parity
of the web route it was extracted from, which has none for this read
either.
"""
from __future__ import annotations

from app.models import TimedSquaresScore


def list_leaderboard(limit: int = 10) -> list[dict]:
    """The top `limit` scores, highest `turns_survived` first, ties broken
    by insertion order (`id` ascending -- earlier score wins a tie), each
    as a `TimedSquaresScore.to_dict()`. Exactly the query both
    `timed_squares/routes.py` routes used to duplicate."""
    rows = (
        TimedSquaresScore.query.order_by(
            TimedSquaresScore.turns_survived.desc(), TimedSquaresScore.id.asc()
        )
        .limit(limit)
        .all()
    )
    return [row.to_dict() for row in rows]

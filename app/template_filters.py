"""Jinja filters shared across templates.

Kept tiny and generic on purpose -- a filter here should be a plain,
reusable formatting rule, not something specific to one page's data.
"""
from __future__ import annotations

from flask import Flask


def format_money(value: float | None) -> str:
    """A dollar figure sized to actually fit a stat-tile.

    Plain "%.2f"-style formatting is fine for everyday numbers, but a
    position's aggregate PV/PnL can genuinely reach six or seven figures,
    and a 9+ character string ("$-143,436") is already wide enough to
    overflow a stat-tile at this font -- since it has no space to wrap
    at, CSS can't break it without either chopping it mid-digit (the
    original bug) or letting it spill out over whatever's next to it
    (a second, easy-to-miss version of the same bug -- the tile itself
    stops growing but the text doesn't). The fix, same as before, is to
    not produce a string that wide in the first place: abbreviate past
    $1M with "M", past $100K with "K" (the threshold where a 6-figure
    number plus a thousands separator -- and possibly a minus sign --
    starts crowding the tile, "$-143,436" being the exact case that
    still overflowed under the old $1M-only cutoff), drop cents past
    $1,000, keep full cents below that. Sign convention matches the rest of the site
    ("$-1,234.56", dollar sign before the minus), not "-$1,234.56".
    """
    if value is None:
        return "n/a"
    magnitude = abs(value)
    # Each tier is checked against its own *already-rounded* candidate
    # value, not the raw magnitude -- e.g. 999,999.00 divided into
    # millions is 0.999999, which rounds to 1.00 at 2dp, so it must
    # abbreviate as "$1.00M" same as 999,999.99 does, not fall through
    # to the K tier and render the confusing "$1,000.0K". Checking
    # top-down this way means by the time a value reaches the K check,
    # it's already confirmed *not* to round up into M, so K's own
    # rounded value can never spill past 999.9.
    millions = value / 1_000_000
    if round(abs(millions), 2) >= 1:
        return f"${millions:,.2f}M"
    # Gated on the rounded whole-dollar magnitude, not on the thousands
    # value's own rounding (99,999 -> 99.999 rounds to "100.0" at 1dp,
    # which would wrongly trigger K for a value that fits fine as-is).
    if round(magnitude) >= 100_000:
        thousands = value / 1_000
        return f"${thousands:,.1f}K"
    if magnitude >= 1_000:
        return f"${value:,.0f}"
    return f"${value:,.2f}"


def register_filters(app: Flask) -> None:
    app.jinja_env.filters["money"] = format_money

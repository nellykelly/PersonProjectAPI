"""Jinja filters shared across templates.

Kept tiny and generic on purpose -- a filter here should be a plain,
reusable formatting rule, not something specific to one page's data.
"""
from __future__ import annotations

from flask import Flask


def format_money(value: float | None) -> str:
    """A dollar figure sized to actually fit a stat-tile.

    Plain "%.2f"-style formatting is fine for everyday numbers, but a
    position's aggregate PV/PnL can genuinely reach seven figures, and a
    12+ character string ("$3756570.57") doesn't fit a stat-tile at any
    reasonable font size -- CSS was previously forcing it to wrap
    mid-digit, which reads as broken rather than merely long (see
    custom.css: .stat-tile .value). The fix is to not produce an
    unreasonably long string in the first place: abbreviate past $1M,
    drop cents past $1,000 (thousands separator still shown), keep full
    cents below that. Sign convention matches the rest of the site
    ("$-1,234.56", dollar sign before the minus), not "-$1,234.56".
    """
    if value is None:
        return "n/a"
    magnitude = abs(value)
    # Checked against the *rounded* magnitude, not the raw one: a value
    # like 999999.99 rounds to 1,000,000 under the "%.0f" branch below,
    # so without this a number that visibly crosses into 7 digits after
    # rounding would still skip the abbreviation meant to prevent
    # exactly that.
    if round(magnitude) >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    if magnitude >= 1_000:
        return f"${value:,.0f}"
    return f"${value:,.2f}"


def register_filters(app: Flask) -> None:
    app.jinja_env.filters["money"] = format_money

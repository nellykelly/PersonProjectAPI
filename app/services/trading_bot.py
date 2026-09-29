"""Read-only view over the Trading Bot project's exported snapshot file.

Trading Bot (S:\\Tradingbot) is a sibling project, not part of this Flask app --
a systematic trend-following stock strategy trading a real Alpaca account
(paper today, possibly small real money later) plus a walk-forward-validated
research record. It has no HTTP API of its own; instead its
`scripts/export_stats.py` periodically writes a JSON snapshot
(`public_stats.json`) that gets synced to wherever this site reads from
(`TRADING_BOT_STATS_PATH`, app/config.py) -- see this blueprint's README for
the sync story.

Reading that file is best-effort and NEVER raises: the file may not exist yet
(nothing has synced), may be mid-write (copied while export_stats.py is still
writing it), or may just be stale (the bot's scheduled job hasn't run
recently). None of those are worth a 500 -- the page says so plainly instead
(`available=False`, `reason=...`), the same graceful-degradation contract
app/services/market_warehouse.py uses for its own external-file read.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

# Trend sleeve rebalances monthly (see the research "strategy" field in the
# snapshot itself) -- this is a display-only estimate of when the next one is
# due, not read from the bot's own scheduler state, so it's always labeled
# "approx." wherever it's shown.
TREND_REBALANCE_CADENCE_DAYS = 30


def _empty(reason: str) -> dict:
    return {
        "available": False,
        "reason": reason,
        "generated_at": None,
        "stale": False,
        "mode": None,
        "live": None,
        "research": None,
    }


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _build_live(raw_live: dict) -> dict:
    positions = raw_live.get("positions") or []
    pending_orders = raw_live.get("pending_orders") or []
    last_trend = _parse_iso(raw_live.get("last_trend_rebalance"))
    next_trend_due = (
        last_trend + timedelta(days=TREND_REBALANCE_CADENCE_DAYS) if last_trend else None
    )
    return {
        "equity": raw_live.get("equity"),
        "cash": raw_live.get("cash"),
        "last_equity": raw_live.get("last_equity"),
        "day_change_pct": raw_live.get("day_change_pct"),
        "positions": positions,
        "position_count": len(positions),
        "pending_orders": pending_orders,
        "pending_order_count": len(pending_orders),
        "last_trend_rebalance": raw_live.get("last_trend_rebalance"),
        "last_alpha_rebalance": raw_live.get("last_alpha_rebalance"),
        "next_trend_rebalance_due": next_trend_due.date().isoformat() if next_trend_due else None,
        "alpha_sleeve_enabled": bool(raw_live.get("alpha_sleeve_enabled")),
    }


def _build_research(raw_research: dict) -> dict:
    by_year_pct = raw_research.get("by_year_pct") or {}
    by_year = [
        {"year": year, "pct": pct}
        for year, pct in sorted(by_year_pct.items(), key=lambda kv: kv[0])
    ]
    return {
        "as_of": raw_research.get("as_of"),
        "methodology": raw_research.get("methodology"),
        "universe": raw_research.get("universe"),
        "strategy": raw_research.get("strategy"),
        "period": raw_research.get("period"),
        "sharpe": raw_research.get("sharpe"),
        "annualized_return_pct": raw_research.get("annualized_return_pct"),
        "total_return_pct": raw_research.get("total_return_pct"),
        "max_drawdown_pct": raw_research.get("max_drawdown_pct"),
        "by_year": by_year,
        "weekly_hit_rate_pct": raw_research.get("weekly_hit_rate_pct"),
        "weekly_mean_return_pct": raw_research.get("weekly_mean_return_pct"),
        "weekly_std_pct": raw_research.get("weekly_std_pct"),
        "notes": raw_research.get("notes") or [],
    }


def get_dashboard_data(stats_path: str, stale_after_hours: float) -> dict:
    """Returns the parsed snapshot, or an `available=False` placeholder with
    a human-readable `reason`. Never raises."""
    if not stats_path:
        return _empty("No TRADING_BOT_STATS_PATH configured.")
    if not os.path.exists(stats_path):
        return _empty("No snapshot file found yet -- the bot hasn't synced its stats here.")

    try:
        with open(stats_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("trading_bot: failed to read %s: %s", stats_path, exc)
        return _empty("The snapshot file couldn't be read (mid-sync, or corrupted).")

    generated_at = _parse_iso(raw.get("generated_at"))
    stale = False
    if generated_at is not None and stale_after_hours:
        age = datetime.now(timezone.utc) - generated_at
        stale = age > timedelta(hours=stale_after_hours)

    return {
        "available": True,
        "reason": None,
        "generated_at": raw.get("generated_at"),
        "stale": stale,
        "mode": raw.get("mode"),
        "live": _build_live(raw.get("live") or {}),
        "research": _build_research(raw.get("research") or {}),
    }

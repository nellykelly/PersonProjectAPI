"""Aggregate/reporting queries over RiskRequest/RiskResult for the
Trading Simulator's risk dashboard (`/projects/trading-simulator/risk-dashboard`)
-- the site-wide "report" view over what the position -> risk request ->
report/live feed system has actually produced, across every leg, not
just the per-leg history panel on one position's own detail page.

Plain SQLAlchemy ORM aggregation, not raw SQL -- unlike Pipeline World's
analytics.py (which specifically showcases hand-written Postgres SQL),
there's no reason to require Postgres here: these are simple counts/
averages/joins that work identically on SQLite (this app's default) and
Postgres alike, matching the trading blueprint's own data model, which
is deliberately portable (see app/models.py).
"""
from __future__ import annotations

from datetime import timedelta

from app.models import Instrument, Leg, RiskRequest, RiskResult, utcnow
from app.services import sse_limits

RECENT_LIMIT = 30


def recent_risk_requests(limit: int = RECENT_LIMIT) -> list[dict]:
    """The most recent risk requests across every leg, newest first --
    each with its Leg/Instrument (for display) and its RiskResult (a
    request can only ever produce one result today, so `.first()` is
    exact, not an approximation)."""
    requests = RiskRequest.query.order_by(RiskRequest.requested_at.desc()).limit(limit).all()

    entries = []
    for request in requests:
        leg = request.leg
        result = request.results.order_by(RiskResult.id.desc()).first()
        entries.append(
            {
                "id": request.id,
                "requested_at": request.requested_at,
                "scenario": request.scenario,
                "status": request.status,
                "leg_id": request.leg_id,
                "strategy_id": request.strategy_id,
                # A position- or book-level run has no single leg, so the
                # dashboard links to the position (or nowhere, for a book
                # run) instead. Without this the row tried to build a leg
                # URL from None and 500'd the whole page.
                "is_position_level": request.is_position_level,
                "is_book_level": request.is_book_level,
                "leg_count": (request.totals or {}).get("leg_count", 1),
                "ticker": leg.ticker if leg else None,
                "kind": leg.kind if leg else None,
                "strike": leg.strike if leg else None,
                "result": result.to_dict() if result else None,
            }
        )
    return entries


def summary_stats() -> dict:
    """Site-wide counters over the whole RiskRequest/RiskResult history,
    plus a couple of averages across currently-open option legs' most
    recent risk result -- not a snapshot of one position, a rollup across
    the whole book, the same shape a real risk desk's summary tiles take."""
    total_requests = RiskRequest.query.count()
    scenario_requests = RiskRequest.query.filter(RiskRequest.scenario.isnot(None)).count()
    failed_requests = RiskRequest.query.filter_by(status="failed").count()

    since = utcnow() - timedelta(hours=24)
    requests_last_24h = RiskRequest.query.filter(RiskRequest.requested_at >= since).count()

    open_option_legs = (
        Leg.query.join(Instrument)
        .filter(Leg.status == "open", Instrument.instrument_type.in_(("call", "put")))
        .all()
    )
    ir_vega_values = []
    delta_values = []
    for leg in open_option_legs:
        latest = RiskResult.query.filter_by(leg_id=leg.id).order_by(RiskResult.id.desc()).first()
        if latest is None:
            continue
        if latest.ir_vega is not None:
            ir_vega_values.append(latest.ir_vega)
        if latest.delta is not None:
            delta_values.append(latest.delta)

    return {
        "total_requests": total_requests,
        "as_of_now_requests": total_requests - scenario_requests,
        "scenario_requests": scenario_requests,
        "failed_requests": failed_requests,
        "requests_last_24h": requests_last_24h,
        "open_option_legs": len(open_option_legs),
        "avg_ir_vega": (sum(ir_vega_values) / len(ir_vega_values)) if ir_vega_values else None,
        "avg_delta": (sum(delta_values) / len(delta_values)) if delta_values else None,
        # Read directly from the same Redis counters sse_limits.py uses to
        # enforce the concurrency cap -- an exact live count of open
        # risk-feed SSE connections right now, not inferred from request
        # rows (a live-feed tick and a manual on-demand request both
        # create the same shape of RiskRequest row, so there'd be no way
        # to tell them apart from the RiskRequest table alone).
        "active_risk_feed_connections": sse_limits.active_connections("risk-feed"),
    }

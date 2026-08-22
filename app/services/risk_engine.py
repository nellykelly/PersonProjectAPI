"""Risk engine: the "risk request -> report" half of position -> risk
request -> report/live data feed.

Rather than recomputing PnL/Greeks inline every time a page renders (the
old behavior, still used by the plain trade-book table), a caller
explicitly *asks* for risk on a Leg -- optionally under a what-if
scenario -- and gets back a persisted RiskRequest + RiskResult pair.
That's what makes "what risk was run, and when, and what did it find"
a real, queryable fact (see RiskRequest.to_dict/RiskResult.to_dict in
app/models.py) instead of something you'd have to reconstruct from a
page load that's long gone.

This app's scale doesn't need a separate async risk-compute worker the
way a real desk's overnight batch grid would (see the Athena platform's
real 50K-node compute grid, cited in the trading blueprint's README) --
`submit_risk_request` runs the request synchronously and returns the
finished result.
"""
from __future__ import annotations

from app.extensions import db
from app.models import Leg, RiskRequest, RiskResult, utcnow
from app.services import market_data, pricing

# Bump size for the scenario-gamma finite-difference re-price -- see
# RiskResult's docstring for why this is a distinct number from the
# closed-form analytical `gamma`.
SCENARIO_GAMMA_BUMP_PCT = 0.01


def _leg_market_value(leg: Leg, spot: float, entry_iv: float | None) -> float:
    return pricing.compute_pnl(
        leg.kind,
        leg.signed_quantity,
        leg.entry_price,
        spot,
        strike=leg.strike,
        expiry=leg.expiry,
        entry_iv=entry_iv,
    )["market_value"]


def submit_risk_request(leg_id: int, scenario: dict | None = None) -> RiskRequest:
    """Creates a RiskRequest for `leg_id`, runs it immediately, and
    returns it (with its one RiskResult already attached via
    `request.results`). Raises `market_data.MarketDataError` if the
    underlying can't be fetched -- the request is still persisted with
    status='failed' so the failed *attempt* itself stays queryable,
    the same way a real risk run failing doesn't erase the fact that it
    was attempted."""
    leg = db.session.get(Leg, leg_id)
    if leg is None:
        raise ValueError(f"no such leg: {leg_id}")

    request = RiskRequest(leg_id=leg_id, scenario=scenario, status="pending")
    db.session.add(request)
    db.session.flush()  # assigns request.id so RiskResult can reference it below

    try:
        _run_risk_request(request, leg)
        request.status = "complete"
    except Exception:
        request.status = "failed"
        db.session.commit()
        raise

    db.session.commit()
    return request


def _run_risk_request(request: RiskRequest, leg: Leg) -> RiskResult:
    scenario = request.scenario or {}
    spot_shock_pct = scenario.get("spot_shock_pct", 0.0)
    vol_shock_pts = scenario.get("vol_shock_pts", 0.0)

    live_spot = market_data.get_last_price(leg.ticker)
    spot = live_spot * (1 + spot_shock_pct)

    entry_iv = leg.entry_iv
    shocked_iv = max(0.0001, entry_iv + vol_shock_pts) if entry_iv is not None else None

    pnl = pricing.compute_pnl(
        leg.kind, leg.signed_quantity, leg.entry_price, spot,
        strike=leg.strike, expiry=leg.expiry, entry_iv=shocked_iv,
    )
    greeks = pricing.position_greeks(
        leg.kind, leg.signed_quantity, spot,
        strike=leg.strike, expiry=leg.expiry, entry_iv=shocked_iv,
    )

    # Scenario gamma: bump-and-revalue around this request's own base
    # spot (the live spot, or the scenario-shocked spot if one was
    # requested) -- see RiskResult's docstring for why this differs from
    # the closed-form `gamma` above.
    h = SCENARIO_GAMMA_BUMP_PCT
    up = _leg_market_value(leg, spot * (1 + h), shocked_iv)
    down = _leg_market_value(leg, spot * (1 - h), shocked_iv)
    scenario_gamma = (up - 2 * pnl["market_value"] + down) / (spot * h) ** 2

    # IR Vega: sensitivity to the *assumed* Hull-White rate-volatility
    # parameter (see pricing.py's Hull-White section) -- 0.0 for stock
    # (no optionality, genuinely zero, not "not modeled"), a real
    # bump-and-revalue number for options.
    ir_vega_value = pricing.position_ir_vega(
        leg.kind, leg.signed_quantity, spot,
        strike=leg.strike, expiry=leg.expiry, entry_iv=shocked_iv,
    )

    result = RiskResult(
        risk_request_id=request.id,
        leg_id=leg.id,
        computed_at=utcnow(),
        underlying_price_used=spot,
        pv=pnl["market_value"],
        pnl=pnl["pnl"],
        pnl_pct=pnl["pnl_pct"],
        delta=greeks["delta"],
        gamma=greeks["gamma"],
        theta=greeks["theta"],
        vega=greeks["vega"],
        ir_delta=greeks["rho"],
        scenario_gamma=scenario_gamma,
        ir_vega=ir_vega_value,
    )
    db.session.add(result)
    db.session.flush()
    return result

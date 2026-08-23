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

The actual pricing (`run_risk_request_job`) runs on a separate `worker`
container over RQ/Redis (see app/services/queue.py), the same
infrastructure Pipeline World's join pipeline already used -- a small
echo of a real desk's overnight batch grid (see the Athena platform's
real 50K-node compute grid, cited in the trading blueprint's README),
just with one worker pool instead of thousands of nodes.
`submit_risk_request` enqueues the job and blocks until the worker
finishes it (polling the RiskRequest row itself, since that's the only
thing both processes actually share), so callers still get a finished
result back synchronously -- distribute the compute, then return the
result.
"""
from __future__ import annotations

import time

from app.extensions import db
from app.models import Leg, RiskRequest, RiskResult, Strategy, utcnow
from app.services import market_data, pricing, risk_models

# How long submit_risk_request will wait for the worker before giving up.
# Generous for a job that's normally sub-second (a market fetch plus pure
# Black-Scholes math) -- this is a ceiling for a stuck/dead worker, not a
# tuned expected latency.
RISK_JOB_TIMEOUT_SECONDS = 20.0
RISK_JOB_POLL_INTERVAL_SECONDS = 0.05

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


# Greeks that sum linearly across legs. PV and PnL are money and also
# sum; pnl_pct deliberately does not appear here, because a ratio of
# ratios is meaningless -- the report recomputes it from the totals.
ADDITIVE_MEASURES = ("pv", "pnl", "delta", "gamma", "theta", "vega", "ir_delta", "ir_vega", "scenario_gamma")


def take_market_snapshot(legs) -> dict:
    """One price per distinct ticker, fetched once for the whole run.

    This is the reason a position-level request exists at all. Pricing
    each leg as it is reached would mark a multi-leg position against
    several different instants, so the net Greeks would describe a
    position that never existed at any single moment. Fetching up front
    and passing the snapshot down means every leg in the report is marked
    against the same market.
    """
    snapshot = {}
    for leg in legs:
        ticker = leg.ticker
        if ticker not in snapshot:
            snapshot[ticker] = market_data.get_last_price(ticker)
    return snapshot


def submit_risk_request(
    *,
    strategy_id: int | None = None,
    leg_id: int | None = None,
    book: bool = False,
    scenario: dict | None = None,
    model_key: str | None = None,
) -> RiskRequest:
    """Runs risk over some scope and returns the persisted request.

    Pass exactly one of:
    - `leg_id` to price a single leg, which is what the live feed and the
      per-leg panel use; the position is then inferred from the leg so the
      request is still attributed to it.
    - `strategy_id` to price every leg in that one position.
    - `book=True` to price every open leg across every position in the
      whole book, bounded into one report -- what "run risk on this
      report" from the all-positions page asks for.

    Arguments are keyword-only on purpose. This used to take a leg id as
    its first positional argument, and a stale `submit_risk_request(42)`
    would now be read as a *position* id -- silently pricing a different
    position instead of failing. Keyword-only turns that into an
    immediate TypeError.

    Raises `market_data.MarketDataError` if a price can't be fetched. The
    request is still persisted with status='failed', so a failed attempt
    stays queryable rather than vanishing -- the same way a real risk run
    failing doesn't erase that it was attempted.
    """
    given = sum([strategy_id is not None, leg_id is not None, book])
    if given != 1:
        raise ValueError("submit_risk_request needs exactly one of strategy_id, leg_id, or book=True")

    if book:
        legs = Leg.query.filter_by(status="open").all()
        if not legs:
            raise ValueError("no open legs across the book to price")
        scope = "book"
    elif leg_id is not None:
        leg = db.session.get(Leg, leg_id)
        if leg is None:
            raise ValueError(f"no such leg: {leg_id}")
        legs = [leg]
        strategy_id = leg.strategy_id
        scope = "leg"
    else:
        strategy = db.session.get(Strategy, strategy_id)
        if strategy is None:
            raise ValueError(f"no such position: {strategy_id}")
        legs = list(strategy.legs)
        if not legs:
            raise ValueError(f"position {strategy_id} has no legs to price")
        scope = "position"

    model = risk_models.get_model(model_key)  # raises UnknownModelError, validated here so a bad
    # name 400s immediately rather than round-tripping to the worker first.

    request = RiskRequest(
        strategy_id=strategy_id,
        leg_id=leg_id,
        scope=scope,
        scenario=scenario,
        status="pending",
        model_key=model.key,
    )
    db.session.add(request)
    # A real commit, not just a flush: the worker prices this in a
    # separate process (its own container) with its own DB session, so it
    # needs this row to already exist and be visible outside this
    # transaction before it can be enqueued.
    db.session.commit()

    from app.services import queue

    queue.enqueue_risk_pricing(request.id)
    _wait_for_risk_request(request.id)

    db.session.refresh(request)
    if request.status == "failed":
        if request.error and request.error.startswith("market_data: "):
            raise market_data.MarketDataError(request.error[len("market_data: ") :])
        raise RuntimeError(request.error or f"risk request {request.id} failed")

    return request


def _wait_for_risk_request(risk_request_id: int) -> None:
    """Blocks until the worker has finished pricing this request.

    The job runs in a different process (a separate container in Docker),
    so there's no in-memory future to await -- the RiskRequest row itself
    is the only thing both sides share, and polling its `status` is the
    simplest thing that's actually correct across that boundary. In
    practice this returns almost immediately: under TESTING, RQ runs the
    job inline inside `enqueue()` (see app/services/queue.py), so the row
    is already done before the first check even happens.
    """
    deadline = time.monotonic() + RISK_JOB_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        # A plain column query, not session.get() -- session.get() can be
        # served from this session's identity map and never touch the
        # database at all, which would just keep re-reading the stale
        # 'pending' value this process already cached.
        status = db.session.query(RiskRequest.status).filter_by(id=risk_request_id).scalar()
        if status != "pending":
            return
        time.sleep(RISK_JOB_POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"risk request {risk_request_id} timed out waiting for the worker")


def run_risk_request_job(risk_request_id: int) -> None:
    """The RQ job body: prices a RiskRequest that's already been created
    and committed, entirely from its own id -- this runs on the `worker`
    container, a different process from the one that enqueued it, so it
    can't be handed the Python objects (`legs`, `model`) the web request
    already resolved and must look everything up fresh from the database.

    Persists status='complete' + totals, or status='failed' + a reason,
    directly onto the row rather than raising -- an exception here can't
    propagate back across the process boundary, so the row itself is the
    only channel back to submit_risk_request's blocking wait.
    """
    request = db.session.get(RiskRequest, risk_request_id)
    if request is None:
        return  # the row is gone; nothing left to price

    if request.leg_id is not None:
        legs = [request.leg]
    elif request.strategy_id is not None:
        legs = list(request.strategy.legs)
    else:
        legs = Leg.query.filter_by(status="open").all()  # book-level

    model = risk_models.get_model(request.model_key)

    try:
        request.totals = _run_risk_request(request, legs, model)
        request.status = "complete"
    except market_data.MarketDataError as exc:
        request.status = "failed"
        request.error = f"market_data: {exc}"
    except Exception as exc:
        request.status = "failed"
        request.error = str(exc)

    db.session.commit()


def _run_risk_request(request: RiskRequest, legs, model) -> dict:
    """Prices every leg off one snapshot and returns the aggregate totals.

    The engine owns everything with a side effect -- the market fetch, the
    scenario shock, the database writes -- and the model owns only the
    maths. Two models answering the same request therefore see byte
    identical inputs, and a model can be tested without a network or a
    database.
    """
    scenario = request.scenario or {}
    spot_shock_pct = scenario.get("spot_shock_pct", 0.0)
    vol_shock_pts = scenario.get("vol_shock_pts", 0.0)

    snapshot = take_market_snapshot(legs)

    totals = {key: 0.0 for key in ADDITIVE_MEASURES}
    cost_basis_total = 0.0
    priced_legs = []

    for leg in legs:
        live_spot = snapshot[leg.ticker]
        # Shocks are whole percent / whole IV points, matching the field
        # names and the form labels. Applying them as raw fractions made
        # every scenario 100x too large.
        spot = live_spot * (1 + spot_shock_pct / 100.0)
        entry_iv = leg.entry_iv
        shocked_iv = max(0.0001, entry_iv + vol_shock_pts / 100.0) if entry_iv is not None else None

        ctx = risk_models.PricingContext(
            leg=leg,
            spot=spot,
            iv=shocked_iv,
            live_spot=live_spot,
            spot_shock_pct=spot_shock_pct,
            vol_shock_pts=vol_shock_pts,
        )

        run = model.run(ctx)
        canonical = run.canonical

        result = RiskResult(
            risk_request_id=request.id,
            leg_id=leg.id,
            computed_at=utcnow(),
            underlying_price_used=canonical.get("underlying_price_used", spot),
            pv=canonical.get("pv"),
            pnl=canonical.get("pnl"),
            pnl_pct=canonical.get("pnl_pct"),
            delta=canonical.get("delta"),
            gamma=canonical.get("gamma"),
            theta=canonical.get("theta"),
            vega=canonical.get("vega"),
            ir_delta=canonical.get("ir_delta"),
            scenario_gamma=canonical.get("scenario_gamma"),
            ir_vega=canonical.get("ir_vega"),
            report={
                "measures": [
                    {
                        "key": m.key,
                        "label": m.label,
                        "value": m.value,
                        "unit": m.unit,
                        "explanation": m.explanation,
                    }
                    for m in run.measures
                ],
                "extras": run.extras,
                "notes": run.notes,
            },
        )
        db.session.add(result)

        for key in ADDITIVE_MEASURES:
            value = canonical.get(key)
            if value is not None:
                totals[key] += value

        cost_basis_total += abs(leg.entry_price * leg.signed_quantity * leg.multiplier)
        priced_legs.append(
            {
                "leg_id": leg.id,
                "strategy_id": leg.strategy_id,
                "instrument_code": leg.instrument.code,
                "ticker": leg.ticker,
                "kind": leg.kind,
                "strike": leg.strike,
                "quantity": leg.signed_quantity,
                "spot_used": spot,
                "pv": canonical.get("pv"),
                "pnl": canonical.get("pnl"),
                "delta": canonical.get("delta"),
                "gamma": canonical.get("gamma"),
                "theta": canonical.get("theta"),
                "vega": canonical.get("vega"),
            }
        )

    db.session.flush()

    # Recomputed from the totals rather than averaged across legs: the
    # mean of per-leg percentages is not the position's return.
    totals["pnl_pct"] = (totals["pnl"] / cost_basis_total * 100.0) if cost_basis_total else 0.0
    totals["cost_basis"] = cost_basis_total
    totals["leg_count"] = len(legs)
    totals["snapshot"] = snapshot
    totals["legs"] = priced_legs
    return totals

"""The model registry, model dispatch, and the generated report page."""
import pytest

from app.services import market_data, risk_engine, risk_models


def _open_call(client, monkeypatch, ticker="AAPL", strike=150.0, iv=0.3):
    from datetime import date, timedelta

    expiry = (date.today() + timedelta(days=60)).strftime("%Y-%m-%d")
    monkeypatch.setattr(
        market_data,
        "get_option_chain",
        lambda tk, e: {"calls": [{"strike": strike, "lastPrice": 5.0, "impliedVolatility": iv}], "puts": []},
    )
    monkeypatch.setattr(market_data, "get_last_price", lambda t, use_cache=True: 150.0)
    client.post(
        "/projects/trading-simulator/open",
        data={"ticker": ticker, "kind": "call", "quantity": "1", "expiry": expiry, "strike": str(strike)},
    )
    from app.models import Instrument, Leg

    return Leg.query.join(Instrument).filter(Instrument.instrument_type == "call").order_by(Leg.id.desc()).first()


# ---------- registry ----------


def test_registry_exposes_both_models_with_full_metadata():
    models = risk_models.list_models()
    assert {m.key for m in models} == {"trader_granular", "full_revalue"}
    for model in models:
        # A report is only meaningful if it can describe the model that
        # produced it, so none of this metadata is optional.
        for attr in ("key", "name", "summary", "method", "good_for", "limitations"):
            assert getattr(model, attr), f"{model.key} is missing {attr}"


def test_get_model_defaults_when_no_key_given():
    assert risk_models.get_model(None).key == risk_models.DEFAULT_MODEL_KEY
    assert risk_models.get_model("").key == risk_models.DEFAULT_MODEL_KEY


def test_get_model_rejects_an_unknown_key():
    with pytest.raises(risk_models.UnknownModelError):
        risk_models.get_model("no_such_model")


# ---------- dispatch ----------


def test_request_records_which_model_ran(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch)
    with client.application.app_context():
        req = risk_engine.submit_risk_request(leg_id=leg.id, model_key="full_revalue")
        assert req.model_key == "full_revalue"


def test_omitting_the_model_key_still_works(client, db, monkeypatch):
    """Callers written before model selection existed must keep working."""
    leg = _open_call(client, monkeypatch)
    with client.application.app_context():
        req = risk_engine.submit_risk_request(leg_id=leg.id)
        assert req.model_key == risk_models.DEFAULT_MODEL_KEY
        assert req.status == "complete"


def test_unknown_model_does_not_persist_a_failed_request(client, db, monkeypatch):
    """The key is validated before the row is written, so a typo is a
    plain rejection rather than junk left behind in the request log."""
    from app.models import RiskRequest

    leg = _open_call(client, monkeypatch)
    with client.application.app_context():
        before = RiskRequest.query.count()
        with pytest.raises(risk_models.UnknownModelError):
            risk_engine.submit_risk_request(leg_id=leg.id, model_key="bogus")
        assert RiskRequest.query.count() == before


def test_both_models_agree_on_convexity(client, db, monkeypatch):
    """They compute it by the same bump, so a disagreement means one of
    them regressed."""
    leg = _open_call(client, monkeypatch)
    with client.application.app_context():
        granular = risk_engine.submit_risk_request(leg_id=leg.id, model_key="trader_granular").results.first()
        revalue = risk_engine.submit_risk_request(leg_id=leg.id, model_key="full_revalue").results.first()
        assert granular.scenario_gamma == pytest.approx(revalue.scenario_gamma, rel=1e-6)


def test_only_full_revalue_produces_a_ladder(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch)
    with client.application.app_context():
        granular = risk_engine.submit_risk_request(leg_id=leg.id, model_key="trader_granular").results.first()
        revalue = risk_engine.submit_risk_request(leg_id=leg.id, model_key="full_revalue").results.first()

        assert not (granular.report.get("extras") or {}).get("ladder")
        ladder = revalue.report["extras"]["ladder"]
        assert len(ladder) == len(risk_models.full_revalue.LADDER_STEPS_PCT)
        # The ladder must be monotonic in spot, or the rungs are mislabelled.
        assert [r["spot"] for r in ladder] == sorted(r["spot"] for r in ladder)
        # The 0% rung is the unshocked base, so it has no P&L against itself.
        base = next(r for r in ladder if r["shock_pct"] == 0)
        assert base["pnl_vs_base"] == pytest.approx(0.0, abs=1e-9)


def test_every_model_fills_the_shared_result_columns(client, db, monkeypatch):
    """The dashboard and live feed read these fixed columns regardless of
    model, so no model may leave them empty."""
    leg = _open_call(client, monkeypatch)
    with client.application.app_context():
        for model in risk_models.list_models():
            result = risk_engine.submit_risk_request(leg_id=leg.id, model_key=model.key).results.first()
            for column in ("pv", "pnl", "delta", "gamma", "theta", "vega", "ir_delta", "scenario_gamma"):
                assert getattr(result, column) is not None, f"{model.key} left {column} empty"


# ---------- report page ----------


def test_report_page_renders_the_model_that_answered(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch)
    resp = client.post(
        f"/projects/trading-simulator/positions/{leg.id}/risk-requests",
        data={"model_key": "full_revalue"},
    )
    payload = resp.get_json()["risk_request"]

    report = client.get(payload["report_url"])
    assert report.status_code == 200
    assert b"Full Revalue" in report.data
    assert b"Revaluation ladder" in report.data


def test_report_page_for_a_granular_run_has_no_ladder(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch)
    resp = client.post(
        f"/projects/trading-simulator/positions/{leg.id}/risk-requests",
        data={"model_key": "trader_granular"},
    )
    report = client.get(resp.get_json()["risk_request"]["report_url"])
    assert report.status_code == 200
    assert b"Trader Granular" in report.data
    assert b"Revaluation ladder" not in report.data


def test_report_page_404s_for_an_unknown_request(client):
    assert client.get("/projects/trading-simulator/risk-reports/999999").status_code == 404


def test_route_rejects_an_unknown_model_with_400(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch)
    resp = client.post(
        f"/projects/trading-simulator/positions/{leg.id}/risk-requests",
        data={"model_key": "not_a_real_model"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


# ---------- position-level risk ----------


def _two_leg_position(client, monkeypatch, db_ext):
    """A position holding two legs on the same underlying, so their
    Greeks genuinely aggregate."""
    from datetime import date, timedelta
    from app.models import Leg, Strategy
    from app.services import instruments

    monkeypatch.setattr(market_data, "get_last_price", lambda t, use_cache=True: 150.0)
    expiry = date.today() + timedelta(days=60)

    strategy = Strategy(session_id="s-multi", name="Two Leg")
    db_ext.session.add(strategy)
    for strike in (150.0, 160.0):
        inst = instruments.get_or_create_instrument("AAPL", "call", strike=strike, expiry=expiry)
        db_ext.session.add(
            Leg(strategy=strategy, instrument=inst, side="buy", quantity=1,
                entry_price=5.0, entry_iv=0.3, entry_underlying_price=150.0)
        )
    db_ext.session.commit()
    return strategy


def test_position_request_prices_every_leg_and_sums_them(app, db, client, monkeypatch):
    with app.app_context():
        strategy = _two_leg_position(client, monkeypatch, db)
        req = risk_engine.submit_risk_request(strategy_id=strategy.id)

        results = req.results.all()
        assert len(results) == 2, "every leg in the position must be priced"
        assert req.is_position_level

        totals = req.totals
        assert totals["leg_count"] == 2
        # Greeks are additive, so the total must be the sum of the legs.
        assert totals["delta"] == pytest.approx(sum(r.delta for r in results), rel=1e-9)
        assert totals["pv"] == pytest.approx(sum(r.pv for r in results), rel=1e-9)
        assert totals["pnl"] == pytest.approx(sum(r.pnl for r in results), rel=1e-9)


def test_all_legs_share_one_market_snapshot(app, db, client, monkeypatch):
    """The reason position-level pricing exists. If each leg fetched its
    own price, a moving market would mark the legs at different instants
    and the net Greeks would describe a position that never existed."""
    with app.app_context():
        strategy = _two_leg_position(client, monkeypatch, db)

        # A price that changes on every single call.
        calls = {"n": 0}

        def drifting_price(ticker, use_cache=True):
            calls["n"] += 1
            return 150.0 + calls["n"]

        monkeypatch.setattr(market_data, "get_last_price", drifting_price)
        req = risk_engine.submit_risk_request(strategy_id=strategy.id)

        spots = {r.underlying_price_used for r in req.results.all()}
        assert len(spots) == 1, f"legs were priced at different spots: {spots}"
        # One fetch for the one distinct ticker, not one per leg.
        assert calls["n"] == 1


def test_position_totals_recompute_return_rather_than_averaging(app, db, client, monkeypatch):
    with app.app_context():
        strategy = _two_leg_position(client, monkeypatch, db)
        totals = risk_engine.submit_risk_request(strategy_id=strategy.id).totals
        expected = totals["pnl"] / totals["cost_basis"] * 100.0
        assert totals["pnl_pct"] == pytest.approx(expected, rel=1e-9)


def test_leg_level_request_still_attributes_to_its_position(app, db, client, monkeypatch):
    leg = _open_call(client, monkeypatch)
    with app.app_context():
        req = risk_engine.submit_risk_request(leg_id=leg.id)
        assert req.leg_id == leg.id
        assert req.strategy_id == leg.strategy_id
        assert not req.is_position_level
        assert req.results.count() == 1


def test_submit_requires_a_target():
    with pytest.raises(ValueError):
        risk_engine.submit_risk_request()


def test_position_route_and_report_render(app, db, client, monkeypatch):
    with app.app_context():
        strategy = _two_leg_position(client, monkeypatch, db)
        strategy_id = strategy.id

    resp = client.post(
        f"/projects/trading-simulator/strategies/{strategy_id}/risk-requests",
        data={"model_key": "trader_granular"},
    )
    assert resp.status_code == 200
    payload = resp.get_json()["risk_request"]
    assert payload["totals"]["leg_count"] == 2

    report = client.get(payload["report_url"])
    assert report.status_code == 200
    assert b"Position totals" in report.data
    assert b"Per-leg breakdown" in report.data


def test_position_entity_is_queryable(app, db, client, monkeypatch):
    with app.app_context():
        strategy = _two_leg_position(client, monkeypatch, db)
        strategy_id = strategy.id

    client.post(f"/projects/trading-simulator/strategies/{strategy_id}/risk-requests")

    page = client.get(f"/projects/trading-simulator/strategies/{strategy_id}")
    assert page.status_code == 200

    api = client.get(f"/projects/trading-simulator/api/strategies/{strategy_id}")
    assert api.status_code == 200
    data = api.get_json()
    assert data["position"]["id"] == strategy_id
    assert len(data["position"]["legs"]) == 2
    assert len(data["risk_requests"]) == 1
    assert data["risk_requests"][0]["totals"]["leg_count"] == 2


def test_risk_dashboard_handles_position_level_rows(app, db, client, monkeypatch):
    """A position-level request has no leg_id. The dashboard used to build
    a leg URL from it unconditionally and 500'd the whole page."""
    with app.app_context():
        strategy = _two_leg_position(client, monkeypatch, db)
        strategy_id = strategy.id
        risk_engine.submit_risk_request(strategy_id=strategy_id)

    resp = client.get("/projects/trading-simulator/risk-dashboard")
    assert resp.status_code == 200
    assert f"Position #{strategy_id}".encode() in resp.data


def test_all_positions_page_lists_positions(app, db, client, monkeypatch):
    with app.app_context():
        strategy = _two_leg_position(client, monkeypatch, db)
        strategy_id = strategy.id

    resp = client.get("/projects/trading-simulator/strategies")
    assert resp.status_code == 200
    assert f"#{strategy_id}".encode() in resp.data
    assert b"Two Leg" in resp.data


def test_trade_book_links_to_all_positions(client):
    resp = client.get("/projects/trading-simulator")
    assert resp.status_code == 200
    assert b"All Positions" in resp.data
    assert b"/projects/trading-simulator/strategies" in resp.data


def test_reports_do_not_claim_an_owning_team(app, db, client, monkeypatch):
    """The models are not owned by a real desk, so a report must not say
    one authored them."""
    leg = _open_call(client, monkeypatch)
    resp = client.post(
        f"/projects/trading-simulator/positions/{leg.id}/risk-requests",
        data={"model_key": "full_revalue"},
    )
    report = client.get(resp.get_json()["risk_request"]["report_url"])
    assert b"Owned by" not in report.data
    assert b"QR - " not in report.data


# ---------- "leg" only means part of a multi-part position ----------
#
# A leg is one component of a MULTI-part strategy (a straddle's two legs,
# a swap's fixed/floating legs) -- a standalone single-instrument trade
# has no siblings and isn't a leg of anything, so the UI must not call it
# one. See https://www.optiontradingpedia.com/options_leg.htm.


def test_single_trade_report_uses_no_leg_language(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch)
    resp = client.post(
        f"/projects/trading-simulator/positions/{leg.id}/risk-requests",
        data={"model_key": "trader_granular"},
    )
    report = client.get(resp.get_json()["risk_request"]["report_url"])
    assert report.status_code == 200
    for phrase in (b"Legs priced", b"Per-leg breakdown", b"Single leg", b"Position totals"):
        assert phrase not in report.data, f"{phrase!r} should not appear for a single-instrument run"
    assert b"Single trade" in report.data


def test_multi_leg_report_still_uses_leg_language(app, db, client, monkeypatch):
    with app.app_context():
        strategy = _two_leg_position(client, monkeypatch, db)
        strategy_id = strategy.id
    resp = client.post(f"/projects/trading-simulator/strategies/{strategy_id}/risk-requests")
    report = client.get(resp.get_json()["risk_request"]["report_url"])
    assert report.status_code == 200
    assert b"Legs priced" in report.data
    assert b"Per-leg breakdown" in report.data
    assert b"Whole position" in report.data


def test_single_leg_strategy_page_uses_no_leg_language(client, db, monkeypatch):
    """A Strategy holding exactly one instrument isn't a multi-leg
    strategy, so its own detail page shouldn't call that instrument
    a leg either."""
    leg = _open_call(client, monkeypatch)
    page = client.get(f"/projects/trading-simulator/strategies/{leg.strategy_id}")
    assert page.status_code == 200
    assert b">Instrument<" in page.data
    assert b">Legs<" not in page.data


def test_multi_leg_strategy_page_still_says_legs(app, db, client, monkeypatch):
    with app.app_context():
        strategy = _two_leg_position(client, monkeypatch, db)
        strategy_id = strategy.id
    page = client.get(f"/projects/trading-simulator/strategies/{strategy_id}")
    assert page.status_code == 200
    assert b">Legs<" in page.data


# ---------- worker-distributed pricing ----------
#
# submit_risk_request now enqueues the actual pricing onto a worker
# (see app/services/risk_engine.run_risk_request_job, app/services/queue.py)
# instead of computing inline. Under TESTING, RQ runs the job synchronously
# inside enqueue() (no real worker needed), so the caller-visible contract
# -- a finished, persisted result comes back -- is unchanged; these tests
# pin that down explicitly so a regression there is caught here, not live.


def test_submit_risk_request_still_returns_a_finished_result(client, db, monkeypatch):
    """Even though pricing now happens in a job, the caller still gets a
    complete, persisted result back -- distribute the compute, then
    return the result."""
    leg = _open_call(client, monkeypatch)
    with client.application.app_context():
        req = risk_engine.submit_risk_request(leg_id=leg.id)
        assert req.status == "complete"
        assert req.totals is not None
        assert req.results.count() == 1


def test_market_data_failure_in_the_worker_still_surfaces_as_marketdataerror(client, db, monkeypatch):
    """The worker runs in a different process and can't raise an
    exception object back across that boundary -- it stores a reason on
    the row instead, and submit_risk_request must translate that back
    into the same MarketDataError callers already handle."""
    leg = _open_call(client, monkeypatch)

    def _boom(ticker, use_cache=True):
        raise market_data.MarketDataError("simulated feed outage")

    monkeypatch.setattr(market_data, "get_last_price", _boom)
    with client.application.app_context():
        with pytest.raises(market_data.MarketDataError, match="simulated feed outage"):
            risk_engine.submit_risk_request(leg_id=leg.id)

        from app.models import RiskRequest

        failed = RiskRequest.query.order_by(RiskRequest.id.desc()).first()
        assert failed.status == "failed"
        assert failed.error == "market_data: simulated feed outage"


# ---------- book-level risk requests ----------


def test_book_level_request_prices_every_open_leg_everywhere(app, db, client, monkeypatch):
    with app.app_context():
        strategy = _two_leg_position(client, monkeypatch, db)
    leg = _open_call(client, monkeypatch, ticker="AAPL", strike=150.0)

    with app.app_context():
        from app.models import Leg

        open_leg_count = Leg.query.filter_by(status="open").count()
        req = risk_engine.submit_risk_request(book=True)
        assert req.scope == "book"
        assert req.is_book_level
        assert req.strategy_id is None
        assert req.leg_id is None
        assert req.results.count() == open_leg_count


def test_book_risk_report_shows_instrument_language_not_leg_language(app, db, client, monkeypatch):
    _two_leg_position(client, monkeypatch, db)
    resp = client.post("/projects/trading-simulator/risk-requests", data={"model_key": "trader_granular"})
    assert resp.status_code == 200
    report = client.get(resp.get_json()["risk_request"]["report_url"])
    assert report.status_code == 200
    assert b"Whole book" in report.data
    assert b"Per-instrument breakdown" in report.data
    assert b"Instruments priced" in report.data


def test_book_request_fails_cleanly_with_nothing_open(client, db):
    with pytest.raises(ValueError, match="no open legs"):
        risk_engine.submit_risk_request(book=True)


def test_submit_risk_request_rejects_more_than_one_scope(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch)
    with pytest.raises(ValueError, match="exactly one"):
        risk_engine.submit_risk_request(leg_id=leg.id, book=True)


# ---------- instrument codes and lookup ----------


def test_instrument_gets_a_real_occ_code_for_options():
    from app.services import instruments

    code = instruments.occ_code("AAPL", "call", 300.0, __import__("datetime").date(2027, 1, 15))
    assert code == "AAPL270115C00300000"


def test_instrument_code_for_stock_is_just_the_ticker():
    from app.services import instruments

    assert instruments.occ_code("AAPL", "stock", None, None) == "AAPL"


def test_instrument_catalog_lists_a_booked_instrument(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch)
    with client.application.app_context():
        code = leg.instrument.code
    page = client.get("/projects/trading-simulator/instruments")
    assert page.status_code == 200
    assert code.encode() in page.data


def test_instrument_search_filters_by_query(client, db, monkeypatch):
    _open_call(client, monkeypatch, ticker="AAPL")
    page = client.get("/projects/trading-simulator/instruments?q=NOPE")
    assert page.status_code == 200
    assert b"No instrument matches" in page.data


def test_instrument_lookup_by_code_finds_every_leg_that_traded_it(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch)
    with client.application.app_context():
        code = leg.instrument.code
    page = client.get(f"/projects/trading-simulator/instruments/{code}")
    assert page.status_code == 200
    assert f"#{leg.id}".encode() in page.data


def test_instrument_lookup_is_case_insensitive(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch)
    with client.application.app_context():
        code = leg.instrument.code
    page = client.get(f"/projects/trading-simulator/instruments/{code.lower()}")
    assert page.status_code == 200


def test_instrument_lookup_404s_for_an_unknown_code(client):
    assert client.get("/projects/trading-simulator/instruments/NOSUCHCODE").status_code == 404


def test_api_instrument_returns_its_legs(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch)
    with client.application.app_context():
        code = leg.instrument.code
    resp = client.get(f"/projects/trading-simulator/api/instruments/{code}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["instrument"]["code"] == code
    assert any(l["id"] == leg.id for l in data["legs"])


def test_all_positions_page_loads_the_js_that_powers_its_own_button(client, db):
    # The "Run risk on the whole book" button is wired up entirely by
    # trading.js (initBookRiskPanel) -- a page that renders the button
    # but forgets to include the script it depends on looks fine to the
    # eye and to a plain route-loads test, but the button silently does
    # nothing when clicked. Caught exactly this way once already.
    resp = client.get("/projects/trading-simulator/strategies")
    assert resp.status_code == 200
    assert b'src="/static/js/trading.js"' in resp.data
    assert b'id="book-run-risk"' in resp.data


# ---------- book overview on the trade-book page ----------


def test_trade_book_shows_empty_state_with_no_book_request_yet(client, db, monkeypatch):
    _open_call(client, monkeypatch)  # a leg-level position exists, but no book-level request
    resp = client.get("/projects/trading-simulator")
    assert b"No whole-book risk request has been run yet" in resp.data
    assert b"book-pnl-chart" not in resp.data


def test_trade_book_shows_overview_from_the_latest_book_request(app, db, client, monkeypatch):
    with app.app_context():
        _two_leg_position(client, monkeypatch, db)
    # submit via the real route, matching how a visitor actually triggers it
    resp = client.post("/projects/trading-simulator/risk-requests", data={"model_key": "trader_granular"})
    assert resp.status_code == 200
    book_request_id = resp.get_json()["risk_request"]["id"]

    page = client.get("/projects/trading-simulator")
    assert f"#{book_request_id}".encode() in page.data
    assert b"Net PV" in page.data
    assert b"book-pnl-chart" in page.data
    assert b"data-legs=" in page.data


def test_trade_book_overview_uses_the_most_recent_book_request_not_the_first(app, db, client, monkeypatch):
    with app.app_context():
        _two_leg_position(client, monkeypatch, db)
    client.post("/projects/trading-simulator/risk-requests", data={"model_key": "trader_granular"})
    second = client.post("/projects/trading-simulator/risk-requests", data={"model_key": "full_revalue"})
    second_id = second.get_json()["risk_request"]["id"]

    page = client.get("/projects/trading-simulator")
    assert f"#{second_id}".encode() in page.data

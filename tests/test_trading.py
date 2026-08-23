from datetime import date, timedelta

import pytest

from app.services import market_data, pricing


# ---------- pure pricing math ----------


def test_black_scholes_call_falls_back_to_intrinsic_at_expiry():
    price = pricing.black_scholes_price("call", spot=110, strike=100, time_to_expiry_years=0, volatility=0.3)
    assert price == 10.0


def test_black_scholes_put_falls_back_to_intrinsic_at_expiry():
    price = pricing.black_scholes_price("put", spot=90, strike=100, time_to_expiry_years=0, volatility=0.3)
    assert price == 10.0


def test_black_scholes_call_price_increases_with_volatility():
    low_vol = pricing.black_scholes_price("call", spot=100, strike=100, time_to_expiry_years=0.5, volatility=0.1)
    high_vol = pricing.black_scholes_price("call", spot=100, strike=100, time_to_expiry_years=0.5, volatility=0.5)
    assert high_vol > low_vol


def test_black_scholes_rejects_bad_kind():
    with pytest.raises(ValueError):
        pricing.black_scholes_price("banana", spot=100, strike=100, time_to_expiry_years=1, volatility=0.2)


def test_compute_pnl_stock_position():
    result = pricing.compute_pnl("stock", quantity=10, entry_price=100.0, current_underlying_price=110.0)
    assert result["pnl"] == pytest.approx(100.0)
    assert result["pnl_pct"] == pytest.approx(10.0)


def test_compute_pnl_option_position_at_expiry_uses_intrinsic():
    expiry = date.today()
    result = pricing.compute_pnl(
        "call",
        quantity=2,
        entry_price=5.0,
        current_underlying_price=120.0,
        strike=100.0,
        expiry=expiry,
        entry_iv=0.4,
        as_of=expiry,
    )
    # intrinsic = 20, mult=100, qty=2 -> market value 4000, cost basis 1000
    assert result["market_value"] == pytest.approx(4000.0)
    assert result["cost_basis"] == pytest.approx(1000.0)
    assert result["pnl"] == pytest.approx(3000.0)


def test_compute_pnl_option_requires_strike_expiry_iv():
    with pytest.raises(ValueError):
        pricing.compute_pnl("call", quantity=1, entry_price=5.0, current_underlying_price=100.0)


# ---------- Greeks ----------
#
# S=100, K=100, T=1yr, r=5%, vol=20% is a standard textbook example
# (e.g. Hull) with well-known published reference values -- used here to
# pin down that the implementation is actually correct, not just
# internally self-consistent.


def test_black_scholes_greeks_call_matches_known_reference_values():
    g = pricing.black_scholes_greeks("call", spot=100, strike=100, time_to_expiry_years=1.0, volatility=0.2, risk_free_rate=0.05)
    assert g["delta"] == pytest.approx(0.6368, abs=1e-4)
    assert g["gamma"] == pytest.approx(0.01876, abs=1e-4)
    assert g["vega"] == pytest.approx(0.3752, abs=1e-4)
    assert g["theta"] == pytest.approx(-0.01757, abs=1e-4)
    assert g["rho"] == pytest.approx(0.5323, abs=1e-4)


def test_black_scholes_greeks_put_matches_known_reference_values():
    g = pricing.black_scholes_greeks("put", spot=100, strike=100, time_to_expiry_years=1.0, volatility=0.2, risk_free_rate=0.05)
    assert g["delta"] == pytest.approx(-0.3632, abs=1e-4)
    assert g["rho"] == pytest.approx(-0.4189, abs=1e-4)
    # gamma/vega are identical in shape for calls and puts.
    assert g["gamma"] == pytest.approx(0.01876, abs=1e-4)
    assert g["vega"] == pytest.approx(0.3752, abs=1e-4)


def test_black_scholes_greeks_rejects_bad_kind():
    with pytest.raises(ValueError):
        pricing.black_scholes_greeks("banana", spot=100, strike=100, time_to_expiry_years=1, volatility=0.2)


@pytest.mark.parametrize(
    "kind,spot,strike,expected_delta",
    [("call", 110, 100, 1.0), ("call", 90, 100, 0.0), ("put", 90, 100, -1.0), ("put", 110, 100, 0.0)],
)
def test_black_scholes_greeks_at_expiry_falls_back_to_boundary_case(kind, spot, strike, expected_delta):
    # No more optionality once time/vol has collapsed -- delta is just
    # in-the-money-or-not, and every second-order Greek is zero.
    g = pricing.black_scholes_greeks(kind, spot=spot, strike=strike, time_to_expiry_years=0, volatility=0.3)
    assert g["delta"] == expected_delta
    assert g["gamma"] == 0.0
    assert g["theta"] == 0.0
    assert g["vega"] == 0.0
    assert g["rho"] == 0.0


def test_black_scholes_greeks_gamma_and_vega_positive_for_calls_and_puts():
    # Both are always non-negative for a long option, regardless of kind
    # -- gamma/vega don't have the sign flip delta/theta/rho do.
    for kind in ("call", "put"):
        g = pricing.black_scholes_greeks(kind, spot=100, strike=100, time_to_expiry_years=0.5, volatility=0.25)
        assert g["gamma"] > 0
        assert g["vega"] > 0


def test_position_greeks_stock_is_trivial():
    g = pricing.position_greeks("stock", quantity=10, current_underlying_price=150.0)
    assert g == {"delta": 10.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}


def test_position_greeks_short_stock_flips_delta_sign():
    g = pricing.position_greeks("stock", quantity=-10, current_underlying_price=150.0)
    assert g["delta"] == -10.0


def test_position_greeks_option_applies_quantity_and_multiplier():
    expiry = date.today() + timedelta(days=365)
    per_unit = pricing.black_scholes_greeks("call", spot=100, strike=100, time_to_expiry_years=1.0, volatility=0.2, risk_free_rate=pricing.RISK_FREE_RATE)
    g = pricing.position_greeks(
        "call", quantity=3, current_underlying_price=100.0, strike=100.0, expiry=expiry, entry_iv=0.2, as_of=date.today()
    )
    # 3 contracts * 100 shares/contract = 300x the per-share Greeks.
    assert g["delta"] == pytest.approx(per_unit["delta"] * 300, rel=1e-3)
    assert g["gamma"] == pytest.approx(per_unit["gamma"] * 300, rel=1e-3)


def test_position_greeks_option_requires_strike_expiry_iv():
    with pytest.raises(ValueError):
        pricing.position_greeks("call", quantity=1, current_underlying_price=100.0)


# ---------- IR Vega / Hull-White stochastic-rate extension ----------


def test_stochastic_rate_price_is_at_least_the_plain_price():
    # Adding rate variance on top of equity variance can only ever
    # increase total variance -- so the stochastic-rate price should
    # never be *lower* than the plain flat-rate price for a long option.
    plain = pricing.black_scholes_price("call", spot=100, strike=100, time_to_expiry_years=1.0, volatility=0.2, risk_free_rate=0.05)
    stochastic = pricing.black_scholes_price_stochastic_rates(
        "call", spot=100, strike=100, time_to_expiry_years=1.0, volatility=0.2, risk_free_rate=0.05
    )
    assert stochastic >= plain


def test_ir_vega_is_positive_for_calls_and_puts():
    # Like gamma/vega (and unlike delta/theta/rho), sensitivity to added
    # variance doesn't flip sign between calls and puts -- more assumed
    # rate-vol can only ever add value to a long option of either kind.
    call_vega = pricing.ir_vega("call", spot=100, strike=100, time_to_expiry_years=1.0, volatility=0.2)
    put_vega = pricing.ir_vega("put", spot=100, strike=100, time_to_expiry_years=1.0, volatility=0.2)
    assert call_vega > 0
    assert put_vega > 0
    assert call_vega == pytest.approx(put_vega, rel=1e-6)


def test_ir_vega_grows_with_time_to_expiry():
    # The Hull-White bond-variance term integrates over [0, T], so it
    # should grow (a lot) with time to expiry -- the whole reason IR Vega
    # matters for long-dated options and is negligible for short-dated ones.
    short_dated = pricing.ir_vega("call", spot=100, strike=100, time_to_expiry_years=0.1, volatility=0.2)
    long_dated = pricing.ir_vega("call", spot=100, strike=100, time_to_expiry_years=5.0, volatility=0.2)
    assert long_dated > short_dated * 100


def test_ir_vega_zero_at_expiry():
    assert pricing.ir_vega("call", spot=110, strike=100, time_to_expiry_years=0, volatility=0.3) == 0.0


def test_ir_vega_rejects_bad_kind():
    with pytest.raises(ValueError):
        pricing.ir_vega("banana", spot=100, strike=100, time_to_expiry_years=1, volatility=0.2)


def test_position_ir_vega_is_zero_for_stock():
    assert pricing.position_ir_vega("stock", quantity=10, current_underlying_price=150.0) == 0.0


def test_position_ir_vega_scales_by_quantity_and_multiplier():
    expiry = date.today() + timedelta(days=365)
    per_share = pricing.ir_vega("call", spot=100, strike=100, time_to_expiry_years=1.0, volatility=0.2)
    position = pricing.position_ir_vega(
        "call", quantity=3, current_underlying_price=100.0, strike=100.0, expiry=expiry, entry_iv=0.2, as_of=date.today()
    )
    assert position == pytest.approx(per_share * 300, rel=1e-3)


def test_position_ir_vega_option_requires_strike_expiry_iv():
    with pytest.raises(ValueError):
        pricing.position_ir_vega("call", quantity=1, current_underlying_price=100.0)


# ---------- routes (market_data monkeypatched -- no live network) ----------


@pytest.fixture(autouse=True)
def fake_market_data(monkeypatch):
    monkeypatch.setattr(market_data, "get_last_price", lambda ticker, use_cache=True: 150.0)
    monkeypatch.setattr(
        market_data,
        "get_history",
        lambda ticker, period="6mo", interval="1d": _FakeHistory(),
    )


class _FakeHistory:
    """Minimal stand-in for a yfinance history DataFrame's .iterrows()."""

    def iterrows(self):
        import datetime as _dt

        for i in range(3):
            ts = _dt.datetime.today() - _dt.timedelta(days=2 - i)
            yield ts, {"Close": 150.0 + i}


def test_trading_index_loads(client):
    resp = client.get("/projects/trading-simulator")
    assert resp.status_code == 200
    assert b"Trading Simulator" in resp.data


def test_open_and_close_stock_position(client, db):
    resp = client.post(
        "/projects/trading-simulator/open",
        data={"ticker": "AAPL", "kind": "stock", "quantity": "5"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"AAPL" in resp.data
    assert b"stock" in resp.data

    from app.models import Instrument, Leg

    leg = Leg.query.join(Instrument).filter(Instrument.underlying_ticker == "AAPL").first()
    assert leg is not None
    assert leg.status == "open"
    assert leg.strategy is not None
    assert leg.strategy.status == "open"
    assert leg.instrument.instrument_type == "stock"

    resp = client.post(
        f"/projects/trading-simulator/positions/{leg.id}/close",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db.session.refresh(leg)
    assert leg.status == "closed"
    db.session.refresh(leg.strategy)
    assert leg.strategy.status == "closed"


def test_open_position_rejects_ticker_off_whitelist(client):
    resp = client.post(
        "/projects/trading-simulator/open",
        data={"ticker": "NOTATICKER", "kind": "stock", "quantity": "1"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"not on the supported ticker list" in resp.data


def test_open_position_enforces_session_cap(client, app):
    app.config["TRADING_MAX_OPEN_POSITIONS_PER_SESSION"] = 1
    client.post("/projects/trading-simulator/open", data={"ticker": "AAPL", "kind": "stock", "quantity": "1"})
    resp = client.post(
        "/projects/trading-simulator/open",
        data={"ticker": "MSFT", "kind": "stock", "quantity": "1"},
        follow_redirects=True,
    )
    assert b"max of 1 open positions" in resp.data


def test_api_quote(client):
    resp = client.get("/projects/trading-simulator/api/quote/AAPL")
    assert resp.status_code == 200
    assert resp.get_json()["price"] == 150.0


# ---------- Instrument/Strategy/Leg booking model ----------


def test_instrument_is_deduplicated_across_repeated_stock_trades(client, db):
    client.post("/projects/trading-simulator/open", data={"ticker": "AAPL", "kind": "stock", "quantity": "1"})
    client.post("/projects/trading-simulator/open", data={"ticker": "AAPL", "kind": "stock", "quantity": "2"})

    from app.models import Instrument, Leg

    instruments = Instrument.query.filter_by(underlying_ticker="AAPL", instrument_type="stock").all()
    assert len(instruments) == 1

    legs = Leg.query.join(Instrument).filter(Instrument.underlying_ticker == "AAPL").all()
    assert len(legs) == 2
    assert all(leg.instrument_id == instruments[0].id for leg in legs)


def test_open_option_position_creates_option_instrument_and_shows_greeks(client, db, monkeypatch):
    from datetime import date, timedelta

    expiry = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
    monkeypatch.setattr(
        market_data,
        "get_option_chain",
        lambda ticker, expiry_str: {
            "calls": [{"strike": 150.0, "lastPrice": 5.0, "impliedVolatility": 0.3}],
            "puts": [],
        },
    )

    resp = client.post(
        "/projects/trading-simulator/open",
        data={"ticker": "AAPL", "kind": "call", "quantity": "1", "expiry": expiry, "strike": "150"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    from app.models import Instrument, Leg

    leg = Leg.query.join(Instrument).filter(Instrument.instrument_type == "call").first()
    assert leg is not None
    assert leg.instrument.strike == 150.0
    assert leg.instrument.exercise_style == "american"
    assert leg.instrument.settlement_type == "physical"
    assert leg.instrument.contract_multiplier == 100

    detail_resp = client.get(f"/projects/trading-simulator/positions/{leg.id}")
    assert detail_resp.status_code == 200
    assert b"Delta" in detail_resp.data
    assert b"Theta" in detail_resp.data


# ---------- risk engine: position -> risk request -> report/live feed ----------


def _open_call(client, ticker="AAPL", strike=150.0, quantity=1, iv=0.3, monkeypatch=None):
    from datetime import date, timedelta

    expiry = (date.today() + timedelta(days=60)).strftime("%Y-%m-%d")
    monkeypatch.setattr(
        market_data,
        "get_option_chain",
        lambda tk, expiry_str: {"calls": [{"strike": strike, "lastPrice": 5.0, "impliedVolatility": iv}], "puts": []},
    )
    client.post(
        "/projects/trading-simulator/open",
        data={"ticker": ticker, "kind": "call", "quantity": str(quantity), "expiry": expiry, "strike": str(strike)},
    )
    from app.models import Instrument, Leg

    return Leg.query.join(Instrument).filter(Instrument.instrument_type == "call").order_by(Leg.id.desc()).first()


def test_submit_risk_request_persists_a_request_and_a_result(app, db, monkeypatch):
    from app.services import risk_engine

    with app.app_context():
        leg = _open_call_direct(monkeypatch)
        request_row = risk_engine.submit_risk_request(leg_id=leg.id)

        assert request_row.status == "complete"
        assert request_row.scenario is None
        result = request_row.results.first()
        assert result is not None
        assert result.pv > 0
        assert result.delta is not None
        assert result.ir_delta is not None  # rho, relabeled
        assert result.scenario_gamma is not None
        assert result.ir_vega is not None  # Hull-White bump-and-revalue -- see RiskResult docstring
        assert result.ir_vega > 0  # a long call gains value as assumed rate-vol rises


def test_ir_delta_equals_rho_from_pricing_module(app, db, monkeypatch):
    from app.services import risk_engine

    with app.app_context():
        leg = _open_call_direct(monkeypatch)
        request_row = risk_engine.submit_risk_request(leg_id=leg.id)
        result = request_row.results.first()

        expected = pricing.position_greeks(
            leg.kind, leg.signed_quantity, result.underlying_price_used,
            strike=leg.strike, expiry=leg.expiry, entry_iv=leg.entry_iv,
        )
        assert result.ir_delta == pytest.approx(expected["rho"], rel=1e-6)


def test_scenario_gamma_is_near_zero_for_a_stock_leg(app, db, monkeypatch):
    from app.models import Instrument, Leg
    from app.services import instruments, risk_engine

    with app.app_context():
        monkeypatch.setattr(market_data, "get_last_price", lambda ticker, use_cache=True: 150.0)
        instrument = instruments.get_or_create_instrument("AAPL", "stock")
        from app.models import Strategy

        strategy = Strategy(session_id="s1", name="Single Leg")
        leg = Leg(strategy=strategy, instrument=instrument, side="buy", quantity=10, entry_price=150.0)
        db.session.add(strategy)
        db.session.add(leg)
        db.session.commit()

        request_row = risk_engine.submit_risk_request(leg_id=leg.id)
        result = request_row.results.first()
        # A stock position's value is linear in spot -- no curvature, so
        # both the analytical gamma and the bump-and-revalue scenario
        # gamma should be (near) zero.
        assert result.gamma == pytest.approx(0.0, abs=1e-9)
        assert result.scenario_gamma == pytest.approx(0.0, abs=1e-6)


def test_submit_risk_request_applies_scenario_shock(app, db, monkeypatch):
    from app.services import risk_engine

    with app.app_context():
        leg = _open_call_direct(monkeypatch)

        baseline = risk_engine.submit_risk_request(leg_id=leg.id).results.first()
        shocked = risk_engine.submit_risk_request(leg_id=leg.id, scenario={"spot_shock_pct": 10, "vol_shock_pts": 0}
        ).results.first()

        # 10 means +10%, not +1000%: shocks are whole percent, matching the
        # field name and the form label.
        assert shocked.underlying_price_used == pytest.approx(baseline.underlying_price_used * 1.1, rel=1e-6)
        # A long call gains value when spot rises.
        assert shocked.pv > baseline.pv


def test_submit_risk_request_raises_for_unknown_leg(app, db):
    from app.services import risk_engine

    with app.app_context():
        with pytest.raises(ValueError):
            risk_engine.submit_risk_request(leg_id=999999)


def _open_call_direct(monkeypatch, ticker="AAPL", strike=150.0, quantity=1, iv=0.3):
    """Books a call Leg directly against the DB (no HTTP round-trip) --
    used by risk_engine tests that need an app context already pushed."""
    from datetime import date, timedelta

    from app.extensions import db
    from app.models import Leg, Strategy
    from app.services import instruments

    monkeypatch.setattr(market_data, "get_last_price", lambda t, use_cache=True: 150.0)
    expiry = date.today() + timedelta(days=60)
    instrument = instruments.get_or_create_instrument(ticker, "call", strike=strike, expiry=expiry)
    strategy = Strategy(session_id="s1", name="Single Leg")
    leg = Leg(strategy=strategy, instrument=instrument, side="buy", quantity=quantity, entry_price=5.0, entry_iv=iv, entry_underlying_price=150.0)
    db.session.add(strategy)
    db.session.add(leg)
    db.session.commit()
    return leg


# ---------- risk request / report routes ----------


def test_risk_request_route_submits_and_report_route_fetches_it_back(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch=monkeypatch)
    assert leg is not None

    resp = client.post(f"/projects/trading-simulator/positions/{leg.id}/risk-requests")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    risk_request_id = data["risk_request"]["id"]
    assert data["risk_request"]["result"]["ir_vega"] is not None

    report_resp = client.get(f"/projects/trading-simulator/api/risk-requests/{risk_request_id}")
    assert report_resp.status_code == 200
    report = report_resp.get_json()["risk_request"]
    assert report["id"] == risk_request_id
    assert report["result"]["delta"] is not None


def test_risk_request_route_accepts_a_scenario(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch=monkeypatch)
    monkeypatch.setattr(market_data, "get_last_price", lambda t, use_cache=True: 150.0)

    resp = client.post(
        f"/projects/trading-simulator/positions/{leg.id}/risk-requests",
        data={"spot_shock_pct": "-10", "vol_shock_pts": "5"},
    )
    assert resp.status_code == 200
    data = resp.get_json()["risk_request"]
    assert data["scenario"] == {"spot_shock_pct": -10.0, "vol_shock_pts": 5.0}

    # -10 has to mean -10%, moving spot to 0.9x. Applied as a raw fraction
    # instead it meant -1000%, which priced the underlying at -2784 -- a
    # negative share price, and every scenario Greek came back 0.
    result = data["result"]
    assert result["underlying_price_used"] > 0
    assert result["underlying_price_used"] == pytest.approx(150.0 * 0.9, rel=1e-6)


def test_risk_request_route_404s_for_unknown_position(client):
    resp = client.post("/projects/trading-simulator/positions/999999/risk-requests")
    assert resp.status_code == 404


def test_multiple_risk_requests_against_one_leg_are_all_individually_queryable(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch=monkeypatch)

    ids = []
    for _ in range(3):
        resp = client.post(f"/projects/trading-simulator/positions/{leg.id}/risk-requests")
        ids.append(resp.get_json()["risk_request"]["id"])

    assert len(set(ids)) == 3  # three distinct, separately persisted requests
    for rid in ids:
        report = client.get(f"/projects/trading-simulator/api/risk-requests/{rid}").get_json()["risk_request"]
        assert report["leg_id"] == leg.id


# ---------- risk dashboard: the site-wide report over every risk request ----------


def test_risk_dashboard_loads_empty(client):
    resp = client.get("/projects/trading-simulator/risk-dashboard")
    assert resp.status_code == 200
    assert b"Risk Dashboard" in resp.data
    # Matches the empty-state element, not its exact wording -- the point
    # is that the dashboard renders an empty state at all, and copy gets
    # reworded without the behaviour changing.
    assert b'class="empty-state"' in resp.data
    assert b"No risk requests" in resp.data


def test_risk_dashboard_shows_requests_from_multiple_positions(client, db, monkeypatch):
    leg_a = _open_call(client, ticker="AAPL", strike=150.0, monkeypatch=monkeypatch)
    leg_b = _open_call(client, ticker="AAPL", strike=160.0, monkeypatch=monkeypatch)

    client.post(f"/projects/trading-simulator/positions/{leg_a.id}/risk-requests")
    client.post(
        f"/projects/trading-simulator/positions/{leg_b.id}/risk-requests",
        data={"spot_shock_pct": "-10", "vol_shock_pts": "0"},
    )

    from app.services import risk_dashboard

    recent = risk_dashboard.recent_risk_requests()
    assert len(recent) == 2
    leg_ids = {row["leg_id"] for row in recent}
    assert leg_ids == {leg_a.id, leg_b.id}
    # One as-of-now, one scenario -- both should show up distinctly.
    scenarios = [row["scenario"] for row in recent]
    assert None in scenarios
    assert any(s is not None for s in scenarios)


def test_risk_dashboard_summary_stats_counts_requests(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch=monkeypatch)
    client.post(f"/projects/trading-simulator/positions/{leg.id}/risk-requests")
    client.post(
        f"/projects/trading-simulator/positions/{leg.id}/risk-requests",
        data={"spot_shock_pct": "5", "vol_shock_pts": "0"},
    )

    from app.services import risk_dashboard

    stats = risk_dashboard.summary_stats()
    assert stats["total_requests"] == 2
    assert stats["as_of_now_requests"] == 1
    assert stats["scenario_requests"] == 1
    assert stats["failed_requests"] == 0
    assert stats["requests_last_24h"] == 2
    assert stats["open_option_legs"] == 1
    assert stats["avg_delta"] is not None
    assert stats["avg_ir_vega"] is not None


def test_risk_dashboard_summary_stats_empty_book(app, db):
    from app.services import risk_dashboard

    with app.app_context():
        stats = risk_dashboard.summary_stats()
        assert stats["total_requests"] == 0
        assert stats["avg_delta"] is None
        assert stats["avg_ir_vega"] is None
        assert stats["active_risk_feed_connections"] == 0


def test_risk_dashboard_route_renders_recent_requests(client, db, monkeypatch):
    leg = _open_call(client, monkeypatch=monkeypatch)
    client.post(f"/projects/trading-simulator/positions/{leg.id}/risk-requests")

    resp = client.get("/projects/trading-simulator/risk-dashboard")
    assert resp.status_code == 200
    assert b"AAPL" in resp.data
    assert b"as-of-now" in resp.data

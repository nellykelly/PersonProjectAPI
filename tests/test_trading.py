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

    from app.models import Position

    position = Position.query.filter_by(ticker="AAPL").first()
    assert position is not None
    assert position.status == "open"

    resp = client.post(
        f"/projects/trading-simulator/positions/{position.id}/close",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    db.session.refresh(position)
    assert position.status == "closed"


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

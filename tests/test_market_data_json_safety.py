"""The option chain has to serialize to *strictly valid* JSON.

yfinance returns pandas NaN for any quote it doesn't have, and illiquid
contracts routinely have no bid. Python's json module writes NaN out as a
bare `NaN` literal, which is a non-standard extension that JSON.parse
rejects outright -- so one missing bid made the browser throw on the whole
response and the option chain silently failed to load with "Could not load
chain".

The trap is that this is invisible from Python: json.loads *accepts* NaN,
so curl-and-parse checks pass while the browser breaks. These tests use a
strict parser that rejects NaN the way a browser does.
"""
import json

import pandas as pd
import pytest

from app.services import market_data


def _strict_loads(text):
    """json.loads that rejects NaN/Infinity, matching JSON.parse."""

    def _reject(constant):
        raise ValueError(f"not valid JSON: bare {constant} literal")

    return json.loads(text, parse_constant=_reject)


class _FakeChain:
    def __init__(self, calls, puts):
        self.calls = calls
        self.puts = puts


class _FakeTicker:
    def __init__(self, chain):
        self._chain = chain

    def option_chain(self, expiry):
        return self._chain


CHAIN_COLUMNS = ["strike", "lastPrice", "bid", "ask", "impliedVolatility", "openInterest"]


def _chain_frame(rows):
    return pd.DataFrame(rows, columns=CHAIN_COLUMNS)


@pytest.fixture
def chain_with_missing_quotes(monkeypatch):
    """A chain shaped like the real thing: one fully-quoted contract and
    one illiquid contract with no bid and no IV, which is where the NaNs
    come from in practice."""
    calls = _chain_frame(
        [
            {"strike": 150.0, "lastPrice": 5.0, "bid": 4.9, "ask": 5.1, "impliedVolatility": 0.30, "openInterest": 100},
            {"strike": 250.0, "lastPrice": 0.01, "bid": float("nan"), "ask": 0.02, "impliedVolatility": float("nan"), "openInterest": 2},
        ]
    )
    puts = _chain_frame(
        [{"strike": 150.0, "lastPrice": 3.0, "bid": float("nan"), "ask": 3.2, "impliedVolatility": 0.28, "openInterest": 5}]
    )
    monkeypatch.setattr(market_data.yf, "Ticker", lambda t: _FakeTicker(_FakeChain(calls, puts)))


def test_option_chain_converts_missing_quotes_to_none(app, chain_with_missing_quotes):
    with app.app_context():
        chain = market_data.get_option_chain("AAPL", "2026-08-24")

    illiquid_call = chain["calls"][1]
    assert illiquid_call["bid"] is None
    assert illiquid_call["impliedVolatility"] is None
    # The quoted values on the same row must survive untouched.
    assert illiquid_call["ask"] == 0.02
    assert illiquid_call["strike"] == 250.0
    assert chain["puts"][0]["bid"] is None


def test_option_chain_serializes_to_json_a_browser_can_parse(app, chain_with_missing_quotes):
    with app.app_context():
        chain = market_data.get_option_chain("AAPL", "2026-08-24")

    payload = json.dumps(chain)
    assert "NaN" not in payload
    assert "Infinity" not in payload
    # Would raise before the fix: json.dumps emits a bare NaN literal,
    # which this parser rejects exactly like JSON.parse does.
    _strict_loads(payload)


def test_chain_route_response_is_strictly_valid_json(client, app, chain_with_missing_quotes):
    resp = client.get("/projects/trading-simulator/api/chain/AAPL/2026-08-24")
    assert resp.status_code == 200
    assert b"NaN" not in resp.data
    _strict_loads(resp.data.decode())


def test_strict_loads_actually_rejects_nan():
    """Guards the guard: if _strict_loads quietly accepted NaN, every
    assertion above would pass no matter how broken the payload was."""
    with pytest.raises(ValueError):
        _strict_loads('{"bid": NaN}')
    # And it still parses ordinary JSON.
    assert _strict_loads('{"bid": 1.5}') == {"bid": 1.5}

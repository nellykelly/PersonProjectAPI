"""Hera's Trading Simulator tools.

Unlike the job-tracker tools, these carry no authorization gate at all --
`build_trading_tools()` always returns the full schema set, because every
action they wrap is already a public, unauthenticated web feature (anyone
can open a position from the plain HTML form). What keeps this safe is
tested here instead:

- `open_position` refuses to write without a valid, one-time
  `confirmation_token` minted by a prior `preview_open_position` call for
  the SAME arguments (session-scoped, server-side -- not forgeable from a
  crafted chat message).
- `open_position`'s write draws from the exact same rate-limit bucket
  (`"trading_open_position"`) as the web route's `POST /open`, so the
  assistant can't hand out a second, unlimited quota alongside the form.
"""
import time

import pytest

from app.extensions import db, limiter
from app.models import Instrument, Leg, Strategy
from app.services import market_data
from app.services.assistant.trading_tools import build_trading_tools, dispatch_trading_tool


@pytest.fixture(autouse=True)
def fake_market_data(monkeypatch):
    monkeypatch.setattr(market_data, "get_last_price", lambda ticker, use_cache=True: 150.0)


def _token_from_preview(preview_text: str) -> str:
    return preview_text.split("confirmation_token=")[1].split()[0]


# --------------------------------------------------------------------------
# build_trading_tools -- schema shape
# --------------------------------------------------------------------------

def test_build_trading_tools_returns_all_five():
    tools = build_trading_tools()
    names = {t["function"]["name"] for t in tools}
    assert names == {
        "get_quote",
        "list_open_positions",
        "get_risk_report",
        "preview_open_position",
        "open_position",
    }


def test_build_trading_tools_takes_no_authorization_argument_and_hands_out_a_fresh_copy():
    a = build_trading_tools()
    a[0]["function"]["name"] = "mutated"
    assert build_trading_tools()[0]["function"]["name"] != "mutated"


def test_open_position_schema_requires_confirmation_token():
    tools = build_trading_tools()
    open_pos = next(t for t in tools if t["function"]["name"] == "open_position")
    assert "confirmation_token" in open_pos["function"]["parameters"]["required"]
    preview = next(t for t in tools if t["function"]["name"] == "preview_open_position")
    assert "confirmation_token" not in preview["function"]["parameters"]["properties"]


# --------------------------------------------------------------------------
# preview -> confirm -> open: the happy path
# --------------------------------------------------------------------------

def test_preview_then_confirm_opens_a_real_position(app, db):
    with app.test_request_context():
        preview = dispatch_trading_tool(
            "preview_open_position", {"ticker": "aapl", "kind": "stock", "quantity": 5}
        )
        assert "confirmation_token=" in preview
        assert "$150.00" in preview
        token = _token_from_preview(preview)

        result = dispatch_trading_tool(
            "open_position",
            {"ticker": "aapl", "kind": "stock", "quantity": 5, "confirmation_token": token},
        )
        assert "Opened" in result
        assert "AAPL" in result

    leg = Leg.query.join(Instrument).filter(Instrument.underlying_ticker == "AAPL").first()
    assert leg is not None
    assert leg.quantity == 5
    assert leg.kind == "stock"
    assert leg.strategy.status == "open"


def test_confirmation_token_is_one_time_use(app, db):
    with app.test_request_context():
        preview = dispatch_trading_tool(
            "preview_open_position", {"ticker": "MSFT", "kind": "stock", "quantity": 1}
        )
        token = _token_from_preview(preview)

        first = dispatch_trading_tool(
            "open_position",
            {"ticker": "MSFT", "kind": "stock", "quantity": 1, "confirmation_token": token},
        )
        assert "Opened" in first

        second = dispatch_trading_tool(
            "open_position",
            {"ticker": "MSFT", "kind": "stock", "quantity": 1, "confirmation_token": token},
        )
        assert "start over" in second.lower()

    assert Strategy.query.count() == 1


# --------------------------------------------------------------------------
# open_position refuses to write without a valid token -- four ways
# --------------------------------------------------------------------------

def test_open_position_refuses_missing_token(app, db):
    with app.test_request_context():
        result = dispatch_trading_tool(
            "open_position", {"ticker": "AAPL", "kind": "stock", "quantity": 1}
        )
        assert "confirmation_token" in result.lower() or "preview" in result.lower()
    assert Strategy.query.count() == 0


def test_open_position_refuses_unknown_token(app, db):
    with app.test_request_context():
        # No preview call happened in this session -- the token was never minted.
        result = dispatch_trading_tool(
            "open_position",
            {"ticker": "AAPL", "kind": "stock", "quantity": 1, "confirmation_token": "not-a-real-token"},
        )
        assert "start over" in result.lower()
    assert Strategy.query.count() == 0


def test_open_position_refuses_expired_token(app, db):
    from flask import session as flask_session

    with app.test_request_context():
        preview = dispatch_trading_tool(
            "preview_open_position", {"ticker": "AAPL", "kind": "stock", "quantity": 1}
        )
        token = _token_from_preview(preview)

        # Force the token's expiry into the past instead of sleeping for
        # real -- same store the module itself writes to.
        store = flask_session["_assistant_confirmations"]
        store[token]["expires_at"] = time.time() - 1
        flask_session.modified = True

        result = dispatch_trading_tool(
            "open_position",
            {"ticker": "AAPL", "kind": "stock", "quantity": 1, "confirmation_token": token},
        )
        assert "expired" in result.lower()
    assert Strategy.query.count() == 0


def test_open_position_refuses_mismatched_args(app, db):
    with app.test_request_context():
        preview = dispatch_trading_tool(
            "preview_open_position", {"ticker": "AAPL", "kind": "stock", "quantity": 1}
        )
        token = _token_from_preview(preview)

        # Same token, but a different quantity than what was previewed.
        result = dispatch_trading_tool(
            "open_position",
            {"ticker": "AAPL", "kind": "stock", "quantity": 999, "confirmation_token": token},
        )
        assert "don't match" in result.lower()
    assert Strategy.query.count() == 0


# --------------------------------------------------------------------------
# rate-limit parity: the web route and the tool share one bucket
# --------------------------------------------------------------------------

def test_open_position_route_and_tool_share_one_rate_limit_bucket(app, db):
    app.config["TRADING_RATE_LIMIT"] = "1 per hour"
    app.config["RATELIMIT_ENABLED"] = True
    limiter.init_app(app)  # rebuild storage now that rate limiting is actually on

    client = app.test_client()

    # First hit goes through the plain web form -- consumes the bucket's
    # only slot for this hour.
    resp = client.post(
        "/projects/trading-simulator/open",
        data={"ticker": "AAPL", "kind": "stock", "quantity": "1"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert Strategy.query.count() == 1

    # Second hit, same IP, goes through the assistant tool instead -- it
    # must be blocked by the SAME bucket the route just spent, not a fresh
    # one of its own.
    with app.test_request_context():
        preview = dispatch_trading_tool(
            "preview_open_position", {"ticker": "MSFT", "kind": "stock", "quantity": 1}
        )
        token = _token_from_preview(preview)
        result = dispatch_trading_tool(
            "open_position",
            {"ticker": "MSFT", "kind": "stock", "quantity": 1, "confirmation_token": token},
        )
        assert "limit" in result.lower()

    # No second position was opened -- the tool's write was actually refused.
    assert Strategy.query.count() == 1


def test_open_position_route_itself_429s_once_the_tool_has_spent_the_bucket(app, db):
    app.config["TRADING_RATE_LIMIT"] = "1 per hour"
    app.config["RATELIMIT_ENABLED"] = True
    limiter.init_app(app)

    # Spend the bucket via the tool first this time, to prove the sharing
    # works in the other direction too.
    with app.test_request_context():
        preview = dispatch_trading_tool(
            "preview_open_position", {"ticker": "AAPL", "kind": "stock", "quantity": 1}
        )
        token = _token_from_preview(preview)
        result = dispatch_trading_tool(
            "open_position",
            {"ticker": "AAPL", "kind": "stock", "quantity": 1, "confirmation_token": token},
        )
        assert "Opened" in result

    client = app.test_client()
    resp = client.post(
        "/projects/trading-simulator/open",
        data={"ticker": "MSFT", "kind": "stock", "quantity": "1"},
    )
    assert resp.status_code == 429
    assert Strategy.query.count() == 1


# --------------------------------------------------------------------------
# get_quote
# --------------------------------------------------------------------------

def test_get_quote_returns_the_price(app, db):
    with app.test_request_context():
        result = dispatch_trading_tool("get_quote", {"ticker": "aapl"})
        assert "AAPL" in result
        assert "150.00" in result


def test_get_quote_rejects_ticker_off_whitelist(app, db):
    with app.test_request_context():
        result = dispatch_trading_tool("get_quote", {"ticker": "NOTATICKER"})
        assert "not on the supported ticker whitelist" in result


# --------------------------------------------------------------------------
# list_open_positions
# --------------------------------------------------------------------------

def _book_a_stock_leg(ticker="AAPL", quantity=3, session_id="s1"):
    from app.services import instruments

    instrument = instruments.get_or_create_instrument(ticker, "stock")
    strategy = Strategy(session_id=session_id, name="Single Leg")
    leg = Leg(
        strategy=strategy, instrument=instrument, side="buy", quantity=quantity,
        entry_price=150.0, entry_underlying_price=150.0,
    )
    db.session.add(strategy)
    db.session.add(leg)
    db.session.commit()
    return leg


def test_list_open_positions_shows_booked_legs(app, db):
    with app.app_context():
        _book_a_stock_leg(ticker="AAPL", quantity=3)

    with app.test_request_context():
        result = dispatch_trading_tool("list_open_positions", {})
        assert "AAPL" in result
        assert "open" in result.lower()


def test_list_open_positions_rejects_bad_limit(app, db):
    with app.test_request_context():
        result = dispatch_trading_tool("list_open_positions", {"limit": "not-a-number"})
        assert "whole number" in result.lower()


# --------------------------------------------------------------------------
# get_risk_report
# --------------------------------------------------------------------------

def test_get_risk_report_prices_a_leg(app, db):
    with app.app_context():
        leg = _book_a_stock_leg(ticker="AAPL", quantity=10)
        leg_id = leg.id

    with app.test_request_context():
        result = dispatch_trading_tool("get_risk_report", {"leg_id": leg_id})
        assert "Risk report" in result
        assert "PV" in result


def test_get_risk_report_rejects_unknown_leg(app, db):
    with app.test_request_context():
        result = dispatch_trading_tool("get_risk_report", {"leg_id": 999999})
        assert "no such leg" in result.lower()


def test_get_risk_report_rejects_ambiguous_scope(app, db):
    with app.test_request_context():
        result = dispatch_trading_tool("get_risk_report", {})
        assert "exactly one" in result.lower()


def test_get_risk_report_tool_and_route_share_one_rate_limit_bucket(app, db):
    # Same story as open_position's bucket-parity tests above, but for the
    # "trading_risk_report" action: the tool and all three web risk-request
    # routes (leg/position/book scope) must draw from one shared bucket,
    # not each get their own.
    app.config["TRADING_RISK_REQUEST_RATE_LIMIT"] = "1 per hour"
    app.config["RATELIMIT_ENABLED"] = True
    limiter.init_app(app)

    with app.app_context():
        leg = _book_a_stock_leg(ticker="AAPL", quantity=10)
        leg_id = leg.id

    client = app.test_client()

    # First hit goes through the plain web route -- spends the bucket's
    # only slot for this hour.
    resp = client.post(f"/projects/trading-simulator/positions/{leg_id}/risk-requests")
    assert resp.status_code == 200

    # Second hit, same IP, goes through the assistant tool -- must be
    # blocked by the SAME bucket the route just spent.
    with app.test_request_context():
        result = dispatch_trading_tool("get_risk_report", {"leg_id": leg_id})
        assert "limit" in result.lower()


def test_get_risk_report_route_itself_429s_once_the_tool_has_spent_the_bucket(app, db):
    app.config["TRADING_RISK_REQUEST_RATE_LIMIT"] = "1 per hour"
    app.config["RATELIMIT_ENABLED"] = True
    limiter.init_app(app)

    with app.app_context():
        leg = _book_a_stock_leg(ticker="AAPL", quantity=10)
        leg_id = leg.id

    # Spend the bucket via the tool first, to prove sharing works in the
    # other direction too.
    with app.test_request_context():
        result = dispatch_trading_tool("get_risk_report", {"leg_id": leg_id})
        assert "Risk report" in result

    client = app.test_client()
    resp = client.post(f"/projects/trading-simulator/positions/{leg_id}/risk-requests")
    assert resp.status_code == 429


# --------------------------------------------------------------------------
# dispatcher never raises
# --------------------------------------------------------------------------

def test_dispatch_unknown_tool_is_a_string(app, db):
    with app.test_request_context():
        assert "Unknown tool" in dispatch_trading_tool("frobnicate", {})


def test_dispatch_bad_json_arguments_is_a_string(app, db):
    with app.test_request_context():
        assert "parse" in dispatch_trading_tool("list_open_positions", "{not json")

"""The write/read actions behind the Trading Simulator's `/open` and
`/strategies` routes, extracted so both the web form and Hera's
`trading_tools` tool-calling layer share one implementation instead of the
tool re-deriving the booking logic from scratch.

This module never touches Flask request/session/flash objects and never
renders anything -- it's pure application logic plus `db.session`. Callers
(a Flask route, or a chat tool) are responsible for turning the exceptions
below into whatever response shape they need (a flash + redirect for the
route, a short string for the model).
"""
from __future__ import annotations

from datetime import datetime

from app.extensions import db
from app.models import Leg, Strategy
from app.services import instruments, market_data
from app.services.market_data import MarketDataError


class TradingActionError(Exception):
    """Raised for any caller-facing validation failure short of a raw
    MarketDataError -- bad kind, quantity out of range, missing/invalid
    option fields, session cap hit, no matching contract. The message is
    written to be shown directly to a user (flashed) or a model."""


def open_strategies_count(session_id: str) -> int:
    # The per-session cap is on booked *strategies* (what a visitor thinks
    # of as "a position I opened"), not legs -- a multi-leg strategy still
    # only counts once even though it holds several Leg rows. Mirrors
    # `trading.routes._open_strategies_count` exactly.
    return Strategy.query.filter_by(session_id=session_id, status="open").count()


def open_position(
    session_id: str,
    ticker: str,
    kind: str,
    quantity,
    strike=None,
    expiry=None,
    *,
    max_open_positions: int,
) -> Leg:
    """Books a new single-leg Strategy and returns its Leg.

    `ticker`/`kind` are normalized (upper/lower-cased and stripped) the
    same way the route used to inline. `quantity` may be an int or a
    string; `strike` a float/str; `expiry` a `date` or an ISO
    'YYYY-MM-DD' string -- whichever shape the caller already has, so a
    JSON tool call and an HTML form both work without a shim.

    Raises `TradingActionError` for any caller-fixable problem (bad kind,
    quantity out of range, session cap, missing/invalid option fields, no
    matching contract) and `market_data.MarketDataError` when a quote or
    chain can't be fetched -- the same two exception shapes the route
    already translated into its flash-and-redirect responses.
    """
    if open_strategies_count(session_id) >= max_open_positions:
        raise TradingActionError(
            f"You've reached the max of {max_open_positions} open positions for this session."
        )

    ticker = (ticker or "").strip().upper()
    kind = (kind or "stock").strip().lower()

    if kind not in ("stock", "call", "put"):
        raise TradingActionError("Invalid position type.")

    try:
        quantity = int(quantity)
        if not (0 < quantity <= 1000):
            raise ValueError
    except (TypeError, ValueError):
        raise TradingActionError("Quantity must be a whole number between 1 and 1000.") from None

    if not market_data.is_valid_ticker(ticker):
        raise TradingActionError(f"'{ticker}' is not on the supported ticker list for this demo.")

    # Not caught here -- MarketDataError propagates to the caller exactly
    # like the route used to let it.
    underlying_price = market_data.get_last_price(ticker)

    entry_iv = None

    if kind == "stock":
        entry_price = underlying_price
        instrument = instruments.get_or_create_instrument(ticker, "stock")
    else:
        if not expiry or strike in (None, ""):
            raise TradingActionError("Options require an expiry date and a strike.")
        try:
            if isinstance(expiry, str):
                expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
                expiry_str = expiry
            else:
                expiry_date = expiry
                expiry_str = expiry.strftime("%Y-%m-%d")
            requested_strike = float(strike)
        except (ValueError, AttributeError):
            raise TradingActionError("Invalid expiry or strike.") from None

        chain = market_data.get_option_chain(ticker, expiry_str)

        chain_side = chain["calls"] if kind == "call" else chain["puts"]
        match = min(chain_side, key=lambda o: abs(o["strike"] - requested_strike)) if chain_side else None
        if match is None:
            raise TradingActionError("No matching contract found for that expiry/strike.")

        matched_strike = match["strike"]
        entry_price = match.get("lastPrice") or 0.0
        entry_iv = match.get("impliedVolatility")
        instrument = instruments.get_or_create_instrument(ticker, kind, strike=matched_strike, expiry=expiry_date)

    # Today's booking flow only ever opens a single-leg strategy -- a
    # multi-leg composer would add more Legs onto an existing open
    # Strategy instead of creating a new one, without changing this at all.
    strategy = Strategy(session_id=session_id, name="Single Leg")
    leg = Leg(
        strategy=strategy,
        instrument=instrument,
        side="buy",
        quantity=quantity,
        entry_price=entry_price,
        entry_iv=entry_iv,
        entry_underlying_price=underlying_price,
    )
    db.session.add(strategy)
    db.session.add(leg)
    db.session.commit()
    return leg


def list_book(limit: int = 20) -> list[Strategy]:
    """Every position in the shared book, newest first -- extracted from
    `trading.routes.strategies_index`'s query."""
    return Strategy.query.order_by(Strategy.opened_at.desc()).limit(limit).all()

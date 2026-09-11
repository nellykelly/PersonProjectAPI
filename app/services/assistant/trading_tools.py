"""The Trading Simulator tools Hera may call -- schemas plus a dispatcher.

Unlike the job-tracker tools (`job_tools.py`, gated to the signed-in,
unlocked owner), these wrap actions that are already public, unauthenticated
web features: anyone can open a position on the shared trade book from the
`/projects/trading-simulator` form today, so `build_trading_tools()` takes
no authorization argument and always returns the full schema list.

Two things keep this from being "the model can do anything a script could
do, just faster":

- **Shared rate-limit bucket.** `open_position` consumes from the same
  `"trading_open_position"` bucket (via `app.services.assistant.rate_limit`)
  that the web route's `POST /open` draws from, so asking the assistant to
  open positions doesn't grant a second, unlimited quota alongside the form.
- **Confirm-before-write.** `open_position` is never called directly with
  fresh, unconfirmed inputs -- the model is expected to call
  `preview_open_position` first, show the user the real quote it returns,
  and only then call `open_position` with the one-time `confirmation_token`
  that preview minted. `open_position` refuses to write without a token that
  checks out (same tool, same arguments, not expired, not already used).
  The token lives in the Flask session, server-side, so it can't be forged
  by a crafted message or a retrieved passage.

`get_quote` and `list_open_positions` are read-only and carry no rate limit
of their own, matching the equivalent web routes (which aren't rate-limited
either). `get_risk_report` consumes from the same `"trading_risk_report"`
bucket (again via `rate_limit.consume`) that all three web risk-request
routes (leg/position/book scope) draw from -- a real shared bucket, not just
a matching limit value, so asking the assistant for a risk report doesn't
grant a second quota alongside the web forms.

Every dispatcher function returns a short string for the model to read and
never raises out of `dispatch_trading_tool`, exactly like `job_tools.py`'s
`dispatch_job_tool`.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

from flask import current_app, session

from app.services import market_data, risk_engine, risk_models, trading_actions
from app.services.assistant import rate_limit
from app.services.market_data import MarketDataError

_MAX_LIST_ROWS = 20
_CONFIRMATION_TTL_SECONDS = 300


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_TICKER = {"type": "string", "description": "Ticker symbol, e.g. AAPL. Must be on this demo's whitelist."}
_KIND = {
    "type": "string",
    "enum": ["stock", "call", "put"],
    "description": "Position type: 'stock', or 'call'/'put' for an option.",
}
_QUANTITY = {"type": "integer", "description": "Whole number of shares/contracts, 1-1000."}
_STRIKE = {"type": "number", "description": "Strike price. Required for call/put, ignored for stock."}
_EXPIRY = {
    "type": "string",
    "description": "Expiry date, ISO YYYY-MM-DD. Required for call/put, ignored for stock.",
}

_OPEN_POSITION_PROPS = {
    "ticker": _TICKER,
    "kind": _KIND,
    "quantity": _QUANTITY,
    "strike": _STRIKE,
    "expiry": _EXPIRY,
}

_TOOL_SPECS = [
    _tool(
        "get_quote",
        "Look up the current price for a ticker, or its option chain for a given expiry. "
        "Read-only, no confirmation needed.",
        {
            "ticker": _TICKER,
            "expiry": {
                "type": "string",
                "description": "Optional ISO YYYY-MM-DD. When given, returns the option chain "
                "for that expiry instead of just the underlying price.",
            },
        },
        ["ticker"],
    ),
    _tool(
        "list_open_positions",
        "List the most recent positions in the shared trade book (every visitor's, not just "
        "this session's -- there are no private books in this demo).",
        {
            "limit": {
                "type": "integer",
                "description": "Max rows to return, defaults to 20.",
            }
        },
        [],
    ),
    _tool(
        "get_risk_report",
        "Run (or fetch) a risk report -- PV, PnL, and Greeks -- for one leg, one whole "
        "position, or the entire book, optionally under a what-if spot/vol shock scenario.",
        {
            "leg_id": {"type": "integer", "description": "Price this one leg. Exactly one of leg_id/strategy_id/book must be given."},
            "strategy_id": {"type": "integer", "description": "Price every leg in this one position."},
            "book": {"type": "boolean", "description": "Price every open leg across the whole shared book."},
            "spot_shock_pct": {"type": "number", "description": "Optional what-if: shock the spot price by this many whole percent (e.g. -10 for -10%)."},
            "vol_shock_pts": {"type": "number", "description": "Optional what-if: shock implied vol by this many whole vol points."},
            "model_key": {"type": "string", "description": "Optional risk model to use; defaults to the standard model if omitted."},
        },
        [],
    ),
    _tool(
        "preview_open_position",
        "Preview opening a new position -- validates the ticker/kind/quantity/strike/expiry, "
        "fetches the real current quote, and returns a one-time confirmation token. Always "
        "call this BEFORE open_position and show the user the quoted price; only call "
        "open_position after the user has explicitly said to go ahead.",
        _OPEN_POSITION_PROPS,
        ["ticker", "kind", "quantity"],
    ),
    _tool(
        "open_position",
        "Actually opens a new position on the shared trade book. Requires a confirmation_token "
        "from a prior preview_open_position call with the SAME ticker/kind/quantity/strike/expiry "
        "-- never call this without first previewing and getting the user's go-ahead.",
        {**_OPEN_POSITION_PROPS, "confirmation_token": {
            "type": "string",
            "description": "The token returned by preview_open_position for these exact same arguments.",
        }},
        ["ticker", "kind", "quantity", "confirmation_token"],
    ),
]


def build_trading_tools() -> list[dict]:
    """The tool schemas to hand the model -- always the full set. These wrap
    actions that are already public web features (anyone can open a
    position from the plain HTML form), so there is no authorization gate
    here the way there is for the job tracker."""
    # Fresh copy each call -- callers must not mutate the module list.
    return [json.loads(json.dumps(spec)) for spec in _TOOL_SPECS]


# --------------------------------------------------------------------------
# Confirmation tokens -- server-side, session-scoped, one-time use
# --------------------------------------------------------------------------

def _open_position_args(args: dict) -> dict:
    """The subset of an open_position/preview_open_position call that has
    to match between the preview and the real write, normalized so
    equivalent-but-differently-typed inputs (e.g. quantity as "2" vs 2)
    still hash the same."""
    return {
        "ticker": (args.get("ticker") or "").strip().upper(),
        "kind": (args.get("kind") or "stock").strip().lower(),
        "quantity": str(args.get("quantity") or "").strip(),
        "strike": str(args.get("strike")) if args.get("strike") not in (None, "") else None,
        "expiry": (args.get("expiry") or "").strip() or None,
    }


def _make_confirmation_token(tool_name: str, args: dict) -> str:
    args_hash = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
    token = secrets.token_urlsafe(16)
    store = session.setdefault("_assistant_confirmations", {})
    store[token] = {"tool": tool_name, "args_hash": args_hash, "expires_at": time.time() + _CONFIRMATION_TTL_SECONDS}
    session.modified = True
    return token


def _check_confirmation_token(tool_name: str, args: dict, token: str) -> str | None:
    """Returns None if valid (and consumes the token, one-time use). Returns an error string otherwise."""
    store = session.get("_assistant_confirmations") or {}
    entry = store.get(token)
    if entry is None:
        return "That confirmation has expired or wasn't found. Let's start over -- tell me what you'd like to open."
    if entry["tool"] != tool_name or entry["expires_at"] < time.time():
        store.pop(token, None)
        session.modified = True
        return "That confirmation has expired. Let's start over -- tell me what you'd like to open."
    args_hash = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
    if entry["args_hash"] != args_hash:
        store.pop(token, None)
        session.modified = True
        return "Those details don't match what was confirmed. Let's start over."
    store.pop(token, None)  # one-time use
    session.modified = True
    return None


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def _render_position_row(leg) -> str:
    bits = [f"{leg.ticker} {leg.kind}", f"qty {leg.quantity}", leg.status]
    return " · ".join(bits) + f"  (id {leg.id}, strategy {leg.strategy_id})"


def _get_quote(args: dict) -> str:
    ticker = (args.get("ticker") or "").strip().upper()
    if not ticker:
        return "Which ticker?"
    expiry = (args.get("expiry") or "").strip() or None

    if not market_data.is_valid_ticker(ticker):
        return f"'{ticker}' is not on the supported ticker whitelist for this demo."

    if expiry:
        chain = market_data.get_option_chain(ticker, expiry)
        calls = chain.get("calls") or []
        puts = chain.get("puts") or []
        return (
            f"{ticker} option chain for {expiry}: {len(calls)} call strike(s), "
            f"{len(puts)} put strike(s). Nearest calls: "
            + ", ".join(f"{c['strike']}@{c.get('lastPrice')}" for c in calls[:5])
        )

    price = market_data.get_last_price(ticker)
    return f"{ticker} last price: ${price:.2f}"


def _list_open_positions(args: dict) -> str:
    limit = args.get("limit")
    try:
        limit = int(limit) if limit not in (None, "") else _MAX_LIST_ROWS
    except (TypeError, ValueError):
        return "The 'limit' value needs to be a whole number."
    limit = max(1, min(limit, 100))

    strategies = trading_actions.list_book(limit=limit)
    if not strategies:
        return "The shared trade book is empty -- no positions opened yet."

    lines = []
    for strategy in strategies:
        for leg in strategy.legs:
            lines.append("- " + _render_position_row(leg))
    if not lines:
        return "The shared trade book is empty -- no positions opened yet."
    return f"{len(strategies)} recent position(s):\n" + "\n".join(lines)


def _get_risk_report(args: dict) -> str:
    leg_id = args.get("leg_id")
    strategy_id = args.get("strategy_id")
    book = bool(args.get("book"))

    given = sum([leg_id is not None, strategy_id is not None, book])
    if given != 1:
        return "Pass exactly one of leg_id, strategy_id, or book=true."

    scenario = None
    spot_shock_pct = args.get("spot_shock_pct")
    vol_shock_pts = args.get("vol_shock_pts")
    if spot_shock_pct not in (None, "") or vol_shock_pts not in (None, ""):
        try:
            scenario = {
                "spot_shock_pct": float(spot_shock_pct or 0),
                "vol_shock_pts": float(vol_shock_pts or 0),
            }
        except (TypeError, ValueError):
            return "spot_shock_pct/vol_shock_pts must be numbers."

    if not rate_limit.consume("trading_risk_report", current_app.config["TRADING_RISK_REQUEST_RATE_LIMIT"]):
        return "You've hit the limit for risk reports for now -- try again later."

    kwargs: dict[str, Any] = {"scenario": scenario, "model_key": args.get("model_key")}
    if leg_id is not None:
        kwargs["leg_id"] = int(leg_id)
    elif strategy_id is not None:
        kwargs["strategy_id"] = int(strategy_id)
    else:
        kwargs["book"] = True

    try:
        risk_request = risk_engine.submit_risk_request(**kwargs)
    except risk_models.UnknownModelError as exc:
        return str(exc)
    except ValueError as exc:
        # e.g. "no such leg: 999999" / "position N has no legs to price" /
        # "no open legs across the book to price" -- caller-fixable, so
        # surface the engine's own message rather than a generic failure.
        return str(exc)
    except MarketDataError as exc:
        return f"Market data unavailable: {exc}"

    totals = risk_request.totals or {}
    return (
        f"Risk report (scope {risk_request.scope}, model {risk_request.model_key}): "
        f"PV ${totals.get('pv', 0):.2f}, PnL ${totals.get('pnl', 0):.2f}, "
        f"delta {totals.get('delta', 0):.4f}, gamma {totals.get('gamma', 0):.4f}, "
        f"theta {totals.get('theta', 0):.4f}, vega {totals.get('vega', 0):.4f} "
        f"(request id {risk_request.id})"
    )


def _preview_open_position(args: dict) -> str:
    ticker = (args.get("ticker") or "").strip().upper()
    kind = (args.get("kind") or "stock").strip().lower()
    if kind not in ("stock", "call", "put"):
        return "Invalid position type -- must be 'stock', 'call', or 'put'."
    try:
        quantity = int(args.get("quantity"))
        if not (0 < quantity <= 1000):
            raise ValueError
    except (TypeError, ValueError):
        return "Quantity must be a whole number between 1 and 1000."

    if not market_data.is_valid_ticker(ticker):
        return f"'{ticker}' is not on the supported ticker list for this demo."

    try:
        price = market_data.get_last_price(ticker)
    except MarketDataError as exc:
        return f"Market data unavailable: {exc}"

    if kind != "stock":
        expiry = (args.get("expiry") or "").strip()
        strike = args.get("strike")
        if not expiry or strike in (None, ""):
            return "Options need an expiry (YYYY-MM-DD) and a strike."

    token = _make_confirmation_token("open_position", _open_position_args(args))
    return (
        f"Ready to open {quantity} {kind} on {ticker} at the current price of ${price:.2f}. "
        f"Confirm and I'll place it -- pass confirmation_token={token} to open_position."
    )


def _open_position(args: dict) -> str:
    token = args.get("confirmation_token")
    if not token:
        return "Missing confirmation_token -- call preview_open_position first."

    error = _check_confirmation_token("open_position", _open_position_args(args), token)
    if error is not None:
        return error

    if not rate_limit.consume("trading_open_position", current_app.config["TRADING_RATE_LIMIT"]):
        return "You've hit the hourly limit for opening positions -- try again later."

    from flask import session as flask_session

    session_id = flask_session.get("session_id") or "anonymous"
    max_open = current_app.config["TRADING_MAX_OPEN_POSITIONS_PER_SESSION"]

    try:
        leg = trading_actions.open_position(
            session_id,
            args.get("ticker"),
            args.get("kind"),
            args.get("quantity"),
            strike=args.get("strike"),
            expiry=args.get("expiry"),
            max_open_positions=max_open,
        )
    except trading_actions.TradingActionError as exc:
        return str(exc)
    except MarketDataError as exc:
        return f"Market data unavailable: {exc}"

    return f"Opened {leg.quantity} {leg.kind} on {leg.ticker}, position id {leg.id} (strategy {leg.strategy_id})."


_HANDLERS = {
    "get_quote": _get_quote,
    "list_open_positions": _list_open_positions,
    "get_risk_report": _get_risk_report,
    "preview_open_position": _preview_open_position,
    "open_position": _open_position,
}


def dispatch_trading_tool(name: str, arguments: Any) -> str:
    """Execute one tool call and return a short string for the model, always.

    Never raises: an unknown tool, a bad argument type, or any unexpected
    exception all come back as text, same contract as `dispatch_job_tool`.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool {name!r}."

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except Exception:  # noqa: BLE001 - ValueError, or RecursionError on pathological nesting
            return f"Could not parse arguments for {name!r}."
    if not isinstance(arguments, dict):
        arguments = {}

    try:
        return handler(arguments)
    except Exception as exc:  # noqa: BLE001 - the model must never see a trace
        return f"That didn't work ({type(exc).__name__})."

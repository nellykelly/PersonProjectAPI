"""Pure pricing/PnL math for the Trading Simulator -- no Flask, no I/O,
so it's trivially unit-testable.

yfinance's option_chain() exposes strikes/bid/ask/open interest/implied
volatility but no Greeks (see docs/build-spec.md, Project 1). Rather than
tracking a live option price we don't have, positions are repriced with a
local Black-Scholes calc fed by the implied volatility yfinance gave us
at entry and the *current* underlying price -- a standard, clearly
documented approximation for a simulator (real desks reprice off a fresh
vol surface; we don't have one, so entry IV is held constant).
"""
from __future__ import annotations

import math
from datetime import date

RISK_FREE_RATE = 0.045  # annualized, flat -- a simplifying assumption, not fetched live
OPTION_MULTIPLIER = 100
STOCK_MULTIPLIER = 1


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def year_fraction(from_date: date, to_date: date) -> float:
    days = (to_date - from_date).days
    return max(days, 0) / 365.0


def black_scholes_price(
    kind: str,
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    volatility: float,
    risk_free_rate: float = RISK_FREE_RATE,
) -> float:
    """Theoretical option value. Falls back to intrinsic value once time
    or volatility has collapsed to (near) zero, since d1/d2 are undefined
    there."""
    if kind not in ("call", "put"):
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")

    intrinsic = max(spot - strike, 0.0) if kind == "call" else max(strike - spot, 0.0)
    if time_to_expiry_years <= 0 or volatility <= 0 or spot <= 0 or strike <= 0:
        return intrinsic

    sqrt_t = math.sqrt(time_to_expiry_years)
    d1 = (
        math.log(spot / strike) + (risk_free_rate + 0.5 * volatility**2) * time_to_expiry_years
    ) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t

    discount = math.exp(-risk_free_rate * time_to_expiry_years)
    if kind == "call":
        return spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
    return strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def current_position_value(
    kind: str,
    current_underlying_price: float,
    strike: float | None,
    expiry: date | None,
    entry_iv: float | None,
    as_of: date | None = None,
) -> float:
    """Per-share/contract value of a position *right now*, before applying
    quantity/multiplier."""
    if kind == "stock":
        return current_underlying_price

    if strike is None or expiry is None or entry_iv is None:
        raise ValueError("options require strike, expiry, and entry_iv")

    as_of = as_of or date.today()
    t = year_fraction(as_of, expiry)
    return black_scholes_price(kind, current_underlying_price, strike, t, entry_iv)


def multiplier_for(kind: str) -> int:
    return OPTION_MULTIPLIER if kind in ("call", "put") else STOCK_MULTIPLIER


def compute_pnl(
    kind: str,
    quantity: int,
    entry_price: float,
    current_underlying_price: float,
    strike: float | None = None,
    expiry: date | None = None,
    entry_iv: float | None = None,
    as_of: date | None = None,
) -> dict:
    """Returns {'current_value': per-unit value, 'pnl': total dollar PnL,
    'pnl_pct': PnL as a % of cost basis}."""
    current_value = current_position_value(kind, current_underlying_price, strike, expiry, entry_iv, as_of)
    mult = multiplier_for(kind)

    cost_basis = entry_price * quantity * mult
    market_value = current_value * quantity * mult
    pnl = market_value - cost_basis
    pnl_pct = (pnl / abs(cost_basis) * 100.0) if cost_basis else 0.0

    return {
        "current_value": current_value,
        "market_value": market_value,
        "cost_basis": cost_basis,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
    }

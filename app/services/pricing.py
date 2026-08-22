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


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


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


def black_scholes_greeks(
    kind: str,
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    volatility: float,
    risk_free_rate: float = RISK_FREE_RATE,
) -> dict:
    """Per-share/contract Greeks (delta, gamma, theta, vega, rho) for one
    option -- the risk sensitivities black_scholes_price doesn't give you
    on its own. Quoted in the conventions traders actually read them in,
    not raw calculus units:

    - delta: dollars of underlying-equivalent exposure per $1 of spot
      move, in [-1, 1] per share.
    - gamma: how fast delta itself changes per $1 of spot move.
    - theta: dollars of value lost per *calendar day* (annual theta / 365),
      not per year -- per-year numbers are correct but useless to read.
    - vega: dollars of value gained per 1 *volatility point* (a move from
      20% to 21% IV, i.e. raw vega / 100), not per 100% of vol.
    - rho: dollars of value gained per 1 *percentage point* of rate move
      (raw rho / 100), same reasoning as vega.

    Falls back to the boundary-case Greeks once time or volatility has
    collapsed to (near) zero, mirroring black_scholes_price's intrinsic-
    value fallback: an expired/zero-vol option has a fixed payoff, so its
    delta is just 0 or 1 (call) / 0 or -1 (put) depending on moneyness,
    and every second-order sensitivity (gamma/theta/vega/rho) is zero --
    there's no more optionality left to be sensitive to anything."""
    if kind not in ("call", "put"):
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")

    if time_to_expiry_years <= 0 or volatility <= 0 or spot <= 0 or strike <= 0:
        if kind == "call":
            delta = 1.0 if spot > strike else 0.0
        else:
            delta = -1.0 if spot < strike else 0.0
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    sqrt_t = math.sqrt(time_to_expiry_years)
    d1 = (
        math.log(spot / strike) + (risk_free_rate + 0.5 * volatility**2) * time_to_expiry_years
    ) / (volatility * sqrt_t)
    d2 = d1 - volatility * sqrt_t
    discount = math.exp(-risk_free_rate * time_to_expiry_years)
    pdf_d1 = _norm_pdf(d1)

    # gamma and vega are identical in shape for calls and puts -- only
    # delta/theta/rho have a sign/term that flips with option kind.
    gamma = pdf_d1 / (spot * volatility * sqrt_t)
    vega = spot * pdf_d1 * sqrt_t / 100.0

    if kind == "call":
        delta = _norm_cdf(d1)
        theta_per_year = -(spot * pdf_d1 * volatility) / (2 * sqrt_t) - risk_free_rate * strike * discount * _norm_cdf(d2)
        rho = strike * time_to_expiry_years * discount * _norm_cdf(d2) / 100.0
    else:
        delta = _norm_cdf(d1) - 1.0
        theta_per_year = -(spot * pdf_d1 * volatility) / (2 * sqrt_t) + risk_free_rate * strike * discount * _norm_cdf(-d2)
        rho = -strike * time_to_expiry_years * discount * _norm_cdf(-d2) / 100.0

    return {"delta": delta, "gamma": gamma, "theta": theta_per_year / 365.0, "vega": vega, "rho": rho}


def position_greeks(
    kind: str,
    quantity: int,
    current_underlying_price: float,
    strike: float | None = None,
    expiry: date | None = None,
    entry_iv: float | None = None,
    as_of: date | None = None,
) -> dict:
    """Position-level Greeks: the per-share/contract Greeks scaled by
    quantity and the contract multiplier (100 for options, 1 for stock) --
    the numbers that actually describe *this booked position's* risk, not
    the textbook per-share figures. A short position (negative quantity)
    correctly flips sign here the same way compute_pnl already does.

    A share of stock has no optionality: its delta is trivially 1 (a $1
    move in the stock is a $1 move in the position) and every other Greek
    is 0 -- there's nothing convex, time-decaying, or vol-sensitive about
    holding shares outright."""
    mult = multiplier_for(kind)

    if kind == "stock":
        per_unit = {"delta": 1.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}
    else:
        if strike is None or expiry is None or entry_iv is None:
            raise ValueError("options require strike, expiry, and entry_iv")
        as_of = as_of or date.today()
        t = year_fraction(as_of, expiry)
        per_unit = black_scholes_greeks(kind, current_underlying_price, strike, t, entry_iv)

    scale = quantity * mult
    return {greek: value * scale for greek, value in per_unit.items()}


def position_ir_vega(
    kind: str,
    quantity: int,
    current_underlying_price: float,
    strike: float | None = None,
    expiry: date | None = None,
    entry_iv: float | None = None,
    as_of: date | None = None,
) -> float:
    """Position-level IR Vega -- the per-share ir_vega() scaled by
    quantity and the contract multiplier, same convention as
    position_greeks. 0.0 for stock (no optionality, nothing for a
    rate-volatility sensitivity to be a derivative of)."""
    if kind == "stock":
        return 0.0

    if strike is None or expiry is None or entry_iv is None:
        raise ValueError("options require strike, expiry, and entry_iv")

    as_of = as_of or date.today()
    t = year_fraction(as_of, expiry)
    per_share = ir_vega(kind, current_underlying_price, strike, t, entry_iv)
    return per_share * quantity * multiplier_for(kind)


# ---------- Hull-White stochastic-rate extension (for IR Vega only) ----------
#
# Every other function in this module treats the risk-free rate as a
# fixed constant (RISK_FREE_RATE) -- deliberately, for a single-stock
# options simulator where rate risk is a minor contributor. IR Vega is
# structurally impossible under that assumption: a constant has no
# volatility to be sensitive to. This section adds a minimal, clearly
# separated stochastic short-rate model *only* to make IR Vega
# computable -- black_scholes_price/black_scholes_greeks/compute_pnl are
# untouched and still use the flat-rate assumption everywhere else in
# the app (PnL, delta/gamma/theta/vega/rho all stay exactly as before).
#
# Model: Hull-White one-factor short rate, dr(t) = (theta(t) - a*r)dt +
# sigma_r*dW(t), assumed UNCORRELATED with the stock's own Brownian
# motion (rho_equity_rate = 0) -- a standard, commonly-cited
# simplification (see Brigo & Mercurio, "Interest Rate Models -- Theory
# and Practice", the hybrid equity-rate option pricing section) used
# when no reliable equity-rate correlation estimate is available, which
# is exactly this app's situation. Neither HULL_WHITE_MEAN_REVERSION nor
# HULL_WHITE_RATE_VOL is fitted to real market data -- there's no
# rate-vol surface in this app's data source (yfinance has no cap/
# swaption vol data) -- both are illustrative assumed constants, the
# same spirit as RISK_FREE_RATE already being a flat assumed constant
# rather than a live curve.
#
# Under rho_equity_rate=0, the standard result is: price the option with
# the *ordinary* Black-Scholes formula, but replace the equity variance
# term sigma^2*T with an adjusted total variance that adds the rate
# process's own contribution:
#   adjusted_variance = sigma^2*T + sigma_r^2 * integral[0,T] B(u,T)^2 du
# where B(u,T) = (1 - exp(-a*(T-u))) / a is the standard Hull-White
# bond-price sensitivity function. That integral has a closed form (see
# _hull_white_bond_variance below).

HULL_WHITE_MEAN_REVERSION = 0.03  # assumed, not calibrated -- see module docstring above
HULL_WHITE_RATE_VOL = 0.01  # assumed 100bp annual short-rate vol -- likewise assumed


def _hull_white_bond_variance(time_to_expiry_years: float, mean_reversion: float, rate_vol: float) -> float:
    """sigma_r^2 * integral[0,T] B(u,T)^2 du, B(u,T) = (1-e^{-a(T-u)})/a.
    Closed form: (1/a^2) * [T - (2/a)(1-e^{-aT}) + (1/(2a))(1-e^{-2aT})]."""
    a = mean_reversion
    t = time_to_expiry_years
    if t <= 0 or a <= 0:
        return 0.0
    integral = (t - (2 / a) * (1 - math.exp(-a * t)) + (1 / (2 * a)) * (1 - math.exp(-2 * a * t))) / (a**2)
    return rate_vol**2 * integral


def black_scholes_price_stochastic_rates(
    kind: str,
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    volatility: float,
    risk_free_rate: float = RISK_FREE_RATE,
    mean_reversion: float = HULL_WHITE_MEAN_REVERSION,
    rate_vol: float = HULL_WHITE_RATE_VOL,
) -> float:
    """black_scholes_price, but under the Hull-White stochastic-rate
    assumption above -- used only to derive ir_vega via bump-and-revalue,
    never for the app's actual displayed price/PnL (that stays on the
    flat-rate black_scholes_price everywhere else)."""
    if time_to_expiry_years <= 0 or volatility <= 0 or spot <= 0 or strike <= 0:
        return black_scholes_price(kind, spot, strike, time_to_expiry_years, volatility, risk_free_rate)

    equity_variance = volatility**2 * time_to_expiry_years
    rate_variance = _hull_white_bond_variance(time_to_expiry_years, mean_reversion, rate_vol)
    adjusted_vol = math.sqrt((equity_variance + rate_variance) / time_to_expiry_years)
    return black_scholes_price(kind, spot, strike, time_to_expiry_years, adjusted_vol, risk_free_rate)


def ir_vega(
    kind: str,
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    volatility: float,
    risk_free_rate: float = RISK_FREE_RATE,
    mean_reversion: float = HULL_WHITE_MEAN_REVERSION,
    rate_vol: float = HULL_WHITE_RATE_VOL,
    bump: float = 0.001,
) -> float:
    """Per-share sensitivity to the assumed Hull-White rate-volatility
    parameter, in dollars per 1 rate-vol *point* (a move from 1.00% to
    1.01% assumed rate vol) -- via central finite difference (the same
    bump-and-revalue approach risk_engine.py's scenario_gamma uses),
    since there's no closed-form derivative worth deriving for a
    parameter this deliberately approximate. Zero once time/vol has
    collapsed to the same boundary case black_scholes_price/
    black_scholes_greeks already fall back to -- an expired or
    zero-vol option has a fixed intrinsic payoff, with no rate-vol
    sensitivity left either."""
    if kind not in ("call", "put"):
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")
    if time_to_expiry_years <= 0 or volatility <= 0 or spot <= 0 or strike <= 0:
        return 0.0

    up = black_scholes_price_stochastic_rates(
        kind, spot, strike, time_to_expiry_years, volatility, risk_free_rate, mean_reversion, rate_vol + bump
    )
    down = black_scholes_price_stochastic_rates(
        kind, spot, strike, time_to_expiry_years, volatility, risk_free_rate, mean_reversion, max(0.0001, rate_vol - bump)
    )
    return (up - down) / (2 * bump) * 0.01  # per 1 rate-vol point (0.01), matching vega/rho's point-quoting convention


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

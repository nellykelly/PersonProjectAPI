"""Trader Granular: the fast, closed-form model a trader watches live.

This is the analytic model. Every number comes straight out of the
Black-Scholes formulas, so a whole book can be revalued in milliseconds
and a blotter can refresh continuously. That speed is the entire point:
it is the model you want answering "what is my delta right now", and it
is deliberately not the model you want answering "what happens in a
crash", because closed-form Greeks are local -- they describe the
position's behaviour for a small move around today's spot, and say
nothing reliable about a large one.
"""
from __future__ import annotations

from app.services import pricing
from app.services.risk_models.base import Measure, ModelRun, PricingContext, RiskModel

# Central-difference bump for the convexity cross-check, as a fraction.
CONVEXITY_BUMP_PCT = 0.01


class TraderGranularModel(RiskModel):
    key = "trader_granular"
    name = "Trader Granular"
    summary = "Fast closed-form Greeks off the Black-Scholes formulas. The model behind a live blotter."
    method = (
        "Prices each instrument analytically and reads the Greeks straight out of the closed-form "
        "Black-Scholes partial derivatives, then scales each one by quantity and contract "
        "multiplier. No repricing loop, so a full book revalues in milliseconds."
    )
    good_for = "Live risk on a screen, hedging decisions, anything that needs an answer immediately."
    limitations = (
        "Every Greek is a local derivative around today's spot, so it only describes small moves. "
        "It cannot tell you what a 20% gap does to this position. Use Full Revalue for that."
    )

    def _value_at(self, ctx: PricingContext, spot: float) -> float:
        leg = ctx.leg
        return pricing.compute_pnl(
            leg.kind, leg.signed_quantity, leg.entry_price, spot,
            strike=leg.strike, expiry=leg.expiry, entry_iv=ctx.iv,
        )["market_value"]

    def run(self, ctx: PricingContext) -> ModelRun:
        leg = ctx.leg

        pnl = pricing.compute_pnl(
            leg.kind, leg.signed_quantity, leg.entry_price, ctx.spot,
            strike=leg.strike, expiry=leg.expiry, entry_iv=ctx.iv,
        )
        greeks = pricing.position_greeks(
            leg.kind, leg.signed_quantity, ctx.spot,
            strike=leg.strike, expiry=leg.expiry, entry_iv=ctx.iv,
        )
        ir_vega = pricing.position_ir_vega(
            leg.kind, leg.signed_quantity, ctx.spot,
            strike=leg.strike, expiry=leg.expiry, entry_iv=ctx.iv,
        )

        # A two-point bump either side of the base spot. This is the one
        # place this model does reprice, and it earns its keep: comparing
        # measured convexity against the analytic gamma above catches a
        # mispriced leg immediately. It is two extra pricing calls, not a
        # ladder, so the model stays fast enough for a live blotter --
        # Full Revalue is the one that walks a whole grid.
        h = CONVEXITY_BUMP_PCT
        up = self._value_at(ctx, ctx.spot * (1 + h))
        down = self._value_at(ctx, ctx.spot * (1 - h))
        scenario_gamma = (up - 2 * pnl["market_value"] + down) / (ctx.spot * h) ** 2

        canonical = {
            "underlying_price_used": ctx.spot,
            "pv": pnl["market_value"],
            "pnl": pnl["pnl"],
            "pnl_pct": pnl["pnl_pct"],
            "delta": greeks["delta"],
            "gamma": greeks["gamma"],
            "theta": greeks["theta"],
            "vega": greeks["vega"],
            "ir_delta": greeks["rho"],
            "ir_vega": ir_vega,
            "scenario_gamma": scenario_gamma,
        }

        measures = [
            Measure("pv", "Present value", canonical["pv"], "$",
                    "What the position is worth at the spot and vol this run used."),
            Measure("pnl", "PnL", canonical["pnl"], "$",
                    "Current value minus what it cost to open."),
            Measure("delta", "Delta", canonical["delta"], "$ per $1",
                    "Dollars gained if the underlying rises $1."),
            Measure("gamma", "Gamma", canonical["gamma"], "delta per $1",
                    "How much Delta itself shifts per $1 move in the underlying."),
            Measure("theta", "Theta", canonical["theta"], "$ per day",
                    "Dollars lost to one more day passing, all else equal."),
            Measure("vega", "Vega", canonical["vega"], "$ per vol pt",
                    "Dollars gained if implied vol rises one point, e.g. 20% to 21%."),
            Measure("ir_delta", "IR Delta (Rho)", canonical["ir_delta"], "$ per 1% rate",
                    "Dollars gained if the risk-free rate rises one percentage point."),
            Measure("ir_vega", "IR Vega", canonical["ir_vega"], "$ per rate-vol pt",
                    "Dollars gained if rates themselves get more volatile, priced under Hull-White."),
            Measure("scenario_gamma", "Convexity (measured)", scenario_gamma, "delta per $1",
                    "Curvature found by repricing +/-1%. Should agree with the analytic Gamma above."),
        ]

        notes = []
        if not ctx.is_option:
            notes.append(
                "This leg is stock, so every second-order Greek is genuinely zero rather than "
                "unavailable. Delta is just the share count."
            )

        return ModelRun(canonical=canonical, measures=measures, notes=notes)

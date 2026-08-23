"""Full Revalue: the stress model that reprices instead of differentiating.

Where Trader Granular reads Greeks out of a formula, this model actually
reprices the position across a ladder of spot shocks and reports the P&L
it observes. That is slower by construction -- it is a pricing call per
rung -- but it answers the question closed-form Greeks genuinely cannot:
what this position does in a large move, where the local derivative at
today's spot has stopped being a useful description.

The convexity it reports (scenario gamma) is measured, not assumed. For
a vanilla option it should land close to the analytic gamma, and the two
disagreeing is a real signal that something is wrong -- which is exactly
why a desk runs both rather than picking one.
"""
from __future__ import annotations

from app.services import pricing
from app.services.risk_models.base import Measure, ModelRun, PricingContext, RiskModel

# Rungs of the spot ladder, as percentage moves from the run's base spot.
LADDER_STEPS_PCT = (-20, -10, -5, -1, 0, 1, 5, 10, 20)

# Central-difference bump for the measured convexity, as a fraction.
CONVEXITY_BUMP_PCT = 0.01


class FullRevalueModel(RiskModel):
    key = "full_revalue"
    name = "Full Revalue"
    summary = "Reprices the position across a ladder of spot shocks. Measures risk instead of differentiating it."
    method = (
        "Reprices each instrument at each rung of a spot ladder from -20% to +20% and reports the P&L "
        "actually observed at each one. Convexity is measured by central difference from a "
        "+/-1% repricing rather than read from a formula."
    )
    good_for = "Stress and scenario work, tail risk, and sanity-checking the analytic Greeks."
    limitations = (
        "One pricing call per rung, so it is far slower than Trader Granular and unsuitable for a "
        "continuously refreshing blotter. The ladder is spot-only: it holds implied vol fixed at "
        "the level this run used, so it does not capture a vol surface reacting to the spot move."
    )

    def _value_at(self, ctx: PricingContext, spot: float) -> float:
        leg = ctx.leg
        return pricing.compute_pnl(
            leg.kind, leg.signed_quantity, leg.entry_price, spot,
            strike=leg.strike, expiry=leg.expiry, entry_iv=ctx.iv,
        )["market_value"]

    def run(self, ctx: PricingContext) -> ModelRun:
        leg = ctx.leg

        base = pricing.compute_pnl(
            leg.kind, leg.signed_quantity, leg.entry_price, ctx.spot,
            strike=leg.strike, expiry=leg.expiry, entry_iv=ctx.iv,
        )
        base_value = base["market_value"]

        ladder = []
        for step in LADDER_STEPS_PCT:
            shocked_spot = ctx.spot * (1 + step / 100.0)
            value = self._value_at(ctx, shocked_spot)
            ladder.append(
                {
                    "shock_pct": step,
                    "spot": shocked_spot,
                    "value": value,
                    "pnl_vs_base": value - base_value,
                }
            )

        # Measured convexity and slope, both by central difference around
        # this run's own base spot.
        h = CONVEXITY_BUMP_PCT
        up = self._value_at(ctx, ctx.spot * (1 + h))
        down = self._value_at(ctx, ctx.spot * (1 - h))
        scenario_gamma = (up - 2 * base_value + down) / (ctx.spot * h) ** 2
        measured_delta = (up - down) / (2 * ctx.spot * h)

        worst = min(ladder, key=lambda r: r["pnl_vs_base"])
        best = max(ladder, key=lambda r: r["pnl_vs_base"])

        # The analytic Greeks are still reported alongside, so the report
        # can show measured-vs-analytic side by side. That comparison is
        # most of this model's diagnostic value.
        greeks = pricing.position_greeks(
            leg.kind, leg.signed_quantity, ctx.spot,
            strike=leg.strike, expiry=leg.expiry, entry_iv=ctx.iv,
        )
        ir_vega = pricing.position_ir_vega(
            leg.kind, leg.signed_quantity, ctx.spot,
            strike=leg.strike, expiry=leg.expiry, entry_iv=ctx.iv,
        )

        canonical = {
            "underlying_price_used": ctx.spot,
            "pv": base_value,
            "pnl": base["pnl"],
            "pnl_pct": base["pnl_pct"],
            "delta": greeks["delta"],
            "gamma": greeks["gamma"],
            "theta": greeks["theta"],
            "vega": greeks["vega"],
            "ir_delta": greeks["rho"],
            "ir_vega": ir_vega,
            "scenario_gamma": scenario_gamma,
        }

        measures = [
            Measure("pv", "Present value", base_value, "$",
                    "Value at the run's base spot, before any ladder shock."),
            Measure("pnl", "PnL", base["pnl"], "$",
                    "Current value minus what it cost to open."),
            Measure("worst_case", "Worst rung", worst["pnl_vs_base"], "$",
                    f"Largest loss on the ladder, at a {worst['shock_pct']}% spot move."),
            Measure("best_case", "Best rung", best["pnl_vs_base"], "$",
                    f"Largest gain on the ladder, at a {best['shock_pct']}% spot move."),
            Measure("measured_delta", "Delta (measured)", measured_delta, "$ per $1",
                    "Slope found by repricing +/-1%, rather than read from the formula."),
            Measure("delta", "Delta (analytic)", greeks["delta"], "$ per $1",
                    "The closed-form Delta, shown for comparison with the measured one."),
            Measure("scenario_gamma", "Convexity (measured)", scenario_gamma, "delta per $1",
                    "Curvature found by repricing +/-1% around the base spot."),
            Measure("gamma", "Gamma (analytic)", greeks["gamma"], "delta per $1",
                    "The closed-form Gamma. It should be close to the measured convexity."),
        ]

        notes = [
            f"Repriced at {len(LADDER_STEPS_PCT)} rungs from {LADDER_STEPS_PCT[0]}% to "
            f"{LADDER_STEPS_PCT[-1]}%, holding implied vol fixed."
        ]
        if not ctx.is_option:
            notes.append(
                "Stock is linear in spot, so the ladder is a straight line and measured convexity "
                "is zero. The ladder is only interesting for options."
            )

        return ModelRun(
            canonical=canonical,
            measures=measures,
            extras={"ladder": ladder, "measured_delta": measured_delta},
            notes=notes,
        )

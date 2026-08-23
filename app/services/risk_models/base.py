"""Shared types every risk model is built from.

A "risk model" here plays the role QR plays on a real desk: the model is
authored once, given a name, and then a trader's risk
request names which model to run. Two requests against the same position
can legitimately return different numbers, because they asked different
models -- and the report records which one answered.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PricingContext:
    """Everything a model needs to price a leg, already resolved.

    The engine does the market-data fetch and applies any scenario shock
    before a model runs, so a model never touches the network and is a
    pure function of this context. That keeps models trivially testable
    and means two models see byte-identical inputs for the same request.
    """

    leg: object  # app.models.Leg -- untyped here to avoid a circular import
    spot: float
    iv: float | None
    live_spot: float
    spot_shock_pct: float
    vol_shock_pts: float

    @property
    def is_option(self) -> bool:
        return self.leg.kind in ("call", "put")


@dataclass(frozen=True)
class Measure:
    """One number in a report, carrying enough context to render itself
    without the template needing to know what it is."""

    key: str
    label: str
    value: float | None
    unit: str
    explanation: str


@dataclass
class ModelRun:
    """What a model hands back.

    `canonical` fills the fixed RiskResult columns the dashboard and the
    live feed already read (pv, pnl, delta, ...). `measures` is the
    model's own ordered output for the report page, which can be richer
    or narrower than those columns. `extras` is free-form JSON for
    anything structural, like a revaluation ladder.
    """

    canonical: dict = field(default_factory=dict)
    measures: list[Measure] = field(default_factory=list)
    extras: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class RiskModel:
    """Base class for a model in the registry.

    Subclasses set the metadata attributes and implement `run`. The
    metadata is what makes a report self-describing: a reader can see
    which model produced the numbers and what it is and is not good for.
    """

    key: str = ""
    name: str = ""
    summary: str = ""
    method: str = ""
    good_for: str = ""
    limitations: str = ""

    def run(self, ctx: PricingContext) -> ModelRun:  # pragma: no cover - interface
        raise NotImplementedError

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "summary": self.summary,
            "method": self.method,
            "good_for": self.good_for,
            "limitations": self.limitations,
        }

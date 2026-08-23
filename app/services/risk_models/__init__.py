"""Registry of the quantitative models a risk request can name.

A request stores the model key it ran, so a report is always able to say
which model produced its numbers. Adding a model is a matter of writing
the class and registering it here -- nothing in the engine, the routes
or the templates hardcodes a model's identity.
"""
from __future__ import annotations

from app.services.risk_models.base import Measure, ModelRun, PricingContext, RiskModel
from app.services.risk_models.full_revalue import FullRevalueModel
from app.services.risk_models.trader_granular import TraderGranularModel

# Ordered: the first entry is what a request gets when it doesn't name a
# model, which keeps every existing caller (and the live feed) working
# without passing one.
_MODELS: dict[str, RiskModel] = {}
for _model in (TraderGranularModel(), FullRevalueModel()):
    _MODELS[_model.key] = _model

DEFAULT_MODEL_KEY = TraderGranularModel.key


class UnknownModelError(ValueError):
    """Raised for a model key that isn't registered. Its own type so a
    route can turn it into a 400 rather than a 500."""


def list_models() -> list[RiskModel]:
    return list(_MODELS.values())


def get_model(key: str | None) -> RiskModel:
    if not key:
        return _MODELS[DEFAULT_MODEL_KEY]
    if key not in _MODELS:
        raise UnknownModelError(f"unknown risk model: {key!r}")
    return _MODELS[key]


__all__ = [
    "DEFAULT_MODEL_KEY",
    "Measure",
    "ModelRun",
    "PricingContext",
    "RiskModel",
    "UnknownModelError",
    "get_model",
    "list_models",
]

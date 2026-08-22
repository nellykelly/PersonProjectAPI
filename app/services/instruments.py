"""Instrument reference-data lookup for the Trading Simulator.

Real trade-booking systems keep one master row per contract and have
every trade reference it, rather than each trade re-embedding its own
copy of the strike/expiry/exercise style -- this is the one place that
rule is enforced: every leg gets its Instrument through
`get_or_create_instrument`, never by constructing an `Instrument` row
directly.
"""
from __future__ import annotations

from datetime import date

from app.extensions import db
from app.models import Instrument


def get_or_create_instrument(
    underlying_ticker: str,
    instrument_type: str,
    strike: float | None = None,
    expiry: date | None = None,
) -> Instrument:
    """Returns the existing Instrument for this exact contract if one's
    already been booked, otherwise creates it. US equity options are
    always American-style and physically settled -- there's no other
    kind in this app's data source (yfinance), so that's filled in here
    rather than asked of the caller."""
    if instrument_type not in ("stock", "call", "put"):
        raise ValueError(f"instrument_type must be 'stock', 'call', or 'put', got {instrument_type!r}")

    existing = Instrument.query.filter_by(
        underlying_ticker=underlying_ticker,
        instrument_type=instrument_type,
        strike=strike,
        expiry=expiry,
    ).first()
    if existing is not None:
        return existing

    is_option = instrument_type in ("call", "put")
    instrument = Instrument(
        underlying_ticker=underlying_ticker,
        instrument_type=instrument_type,
        strike=strike,
        expiry=expiry,
        exercise_style="american" if is_option else None,
        settlement_type="physical" if is_option else None,
        contract_multiplier=100 if is_option else 1,
    )
    db.session.add(instrument)
    db.session.flush()  # assigns instrument.id without a full commit, so the caller can attach a Leg to it in the same transaction
    return instrument

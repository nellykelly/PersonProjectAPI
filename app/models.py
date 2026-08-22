from datetime import datetime, timezone

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class Position(db.Model):
    """A single simulated trade in the shared, anonymous public trade book.

    No user accounts -- `session_id` is a random UUID stored in a cookie,
    used only to cap how many open positions one visitor can hold at once.
    All positions (across all visitors) are visible to everyone.
    """

    __tablename__ = "positions"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(36), nullable=False, index=True)

    ticker = db.Column(db.String(10), nullable=False, index=True)
    kind = db.Column(db.String(4), nullable=False)  # 'stock' | 'call' | 'put'
    quantity = db.Column(db.Integer, nullable=False)

    strike = db.Column(db.Float, nullable=True)
    expiry = db.Column(db.Date, nullable=True)

    entry_price = db.Column(db.Float, nullable=False)
    entry_iv = db.Column(db.Float, nullable=True)
    entry_underlying_price = db.Column(db.Float, nullable=True)

    opened_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    closed_at = db.Column(db.DateTime, nullable=True)
    close_price = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(6), nullable=False, default="open")  # 'open' | 'closed'

    @property
    def is_option(self) -> bool:
        return self.kind in ("call", "put")

    @property
    def multiplier(self) -> int:
        return 100 if self.is_option else 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ticker": self.ticker,
            "kind": self.kind,
            "quantity": self.quantity,
            "strike": self.strike,
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "entry_price": self.entry_price,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "close_price": self.close_price,
            "status": self.status,
        }


class PriceCache(db.Model):
    """Short-TTL cache of the last fetched underlying price per ticker.

    Cuts down on repeat yfinance calls (free-tier rate limits) when many
    visitors are viewing/polling the same shared trade book at once.
    """

    __tablename__ = "price_cache"

    ticker = db.Column(db.String(10), primary_key=True)
    price = db.Column(db.Float, nullable=False)
    fetched_at = db.Column(db.DateTime, nullable=False, default=utcnow)

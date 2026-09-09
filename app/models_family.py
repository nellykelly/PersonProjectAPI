"""Models for the private /family suite (app/blueprints/family).

Kept in their own module so app/models.py doesn't balloon; it does
``from app.models_family import *`` at its end so ``flask db`` and
``db.create_all()`` still see them. Same conventions as the rest of
models.py: explicit ``__tablename__``, ``utcnow`` defaults, ``db.Text``
for free text, bounded ``db.String(N)`` for fixed vocab (sized to the
longest literal + headroom -- ``tests/test_models_column_widths.py``
enforces this), ``db.JSON(none_as_null=True)`` for structured data, and a
Python ``default=`` on every NOT NULL column so a dev SQLite file
auto-heals.

Members: exactly two rows, seeded by the migration -- ``m1`` = Nelson (the
primary maker of this household's Hera), ``m2`` = Savannah ("Sav").
"""
from app.extensions import db
from app.models import utcnow

__all__ = [
    "FamilyMember",
    "FamilyCalendarEvent",
    "FamilyGroceryOrder",
    "FamilyGroceryItem",
    "FamilyMemory",
    "FamilyChatMessage",
    "FAMILY_MEMBER_SLUGS",
    "FAMILY_MEMBER_NAME_MAX",
    "FAMILY_MEMBER_ACCENT_MAX",
    "FAMILY_GROCERY_ORDER_STATUSES",
    "FAMILY_CHAT_ROLES",
    "FAMILY_CHAT_TOOL_CALL_ID_MAX",
    "FAMILY_RECURRENCE_FREQS",
]

FAMILY_MEMBER_SLUGS = ("m1", "m2")
FAMILY_MEMBER_NAME_MAX = 40       # `flask family rename` truncates to this
FAMILY_MEMBER_ACCENT_MAX = 16     # a CSS token name or a #hex
FAMILY_GROCERY_ORDER_STATUSES = ("open", "archived")          # longest 8
FAMILY_CHAT_ROLES = ("user", "assistant", "tool")            # longest 9
FAMILY_CHAT_TOOL_CALL_ID_MAX = 64  # provider tool-call id, truncated on write
FAMILY_RECURRENCE_FREQS = ("weekly", "monthly")


class FamilyMember(db.Model):
    """One of the two people. Seeded; renamed via ``flask family rename``."""

    __tablename__ = "family_members"
    __table_args__ = (db.UniqueConstraint("slug", name="uq_family_members_slug"),)

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(8), nullable=False, default="m1")   # 'm1' | 'm2'
    name = db.Column(db.String(40), nullable=False, default="")
    accent = db.Column(db.String(16), nullable=False, default="leaf")  # CSS token / hex
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    @classmethod
    def by_slug(cls, slug):
        return cls.query.filter_by(slug=slug).first()

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"<FamilyMember {self.slug} {self.name!r}>"


class FamilyCalendarEvent(db.Model):
    """A calendar entry. ``recurrence`` null => one-off; otherwise a small
    rule dict (see app/services/family/calendar.py)."""

    __tablename__ = "family_calendar_events"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.Text, nullable=False, default="")
    starts_on = db.Column(db.Date, nullable=False)
    starts_at = db.Column(db.Time, nullable=True)      # null => all-day
    ends_at = db.Column(db.Time, nullable=True)
    all_day = db.Column(db.Boolean, nullable=False, default=True)
    note = db.Column(db.Text, nullable=True)
    recurrence = db.Column(db.JSON(none_as_null=True), nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("family_members.id"), nullable=False, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    created_by = db.relationship("FamilyMember", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"<FamilyCalendarEvent {self.starts_on} {self.title!r}>"


class FamilyGroceryOrder(db.Model):
    """A shopping trip. Invariant (enforced in the service, not the DB):
    at most one row with ``status='open'`` at a time."""

    __tablename__ = "family_grocery_orders"

    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(10), nullable=False, default="open")  # 'open' | 'archived'
    title = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("family_members.id"), nullable=False, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    archived_at = db.Column(db.DateTime, nullable=True)

    items = db.relationship(
        "FamilyGroceryItem",
        backref="order",
        lazy="dynamic",
        cascade="all, delete-orphan",
        order_by="FamilyGroceryItem.position",
    )
    created_by = db.relationship("FamilyMember", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"<FamilyGroceryOrder {self.id} {self.status}>"


class FamilyGroceryItem(db.Model):
    __tablename__ = "family_grocery_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer,
        db.ForeignKey("family_grocery_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.Text, nullable=False, default="")
    quantity = db.Column(db.Text, nullable=True)
    note = db.Column(db.Text, nullable=True)
    got = db.Column(db.Boolean, nullable=False, default=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    added_by_id = db.Column(
        db.Integer, db.ForeignKey("family_members.id"), nullable=False, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    added_by = db.relationship("FamilyMember", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"<FamilyGroceryItem {self.name!r} got={self.got}>"


class FamilyMemory(db.Model):
    """A shared fact Hera keeps across conversations. ``created_by_id`` null
    => Hera wrote it herself."""

    __tablename__ = "family_memories"

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False, default="")
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("family_members.id"), nullable=True, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    created_by = db.relationship("FamilyMember", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"<FamilyMemory {self.content[:40]!r}>"


class FamilyChatMessage(db.Model):
    """One turn in a member's private thread with Hera. Doubles as the
    transcript and the record -- there is no separate analytics table.
    ``member_id`` is the thread key."""

    __tablename__ = "family_chat_messages"

    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(
        db.Integer, db.ForeignKey("family_members.id"), nullable=False, index=True
    )
    role = db.Column(db.String(10), nullable=False, default="user")  # 'user'|'assistant'|'tool'
    content = db.Column(db.Text, nullable=False, default="")
    tool_calls = db.Column(db.JSON(none_as_null=True), nullable=True)
    tool_call_id = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    member = db.relationship("FamilyMember", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return f"<FamilyChatMessage m={self.member_id} {self.role}>"

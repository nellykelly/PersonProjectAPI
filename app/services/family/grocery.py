"""The one writer + reader for the family grocery list.

Modelled on how a household actually shops: exactly one *open* order at a
time, plus a history of archived ones. "Start new" archives the current
open order and opens an empty one; "copy forward" does the same but
pre-fills the new order from a chosen past one (names + quantities, not
the ticked-off state). The single-open invariant is enforced here, in one
transaction, not by the database.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db
from app.models import FamilyGroceryItem, FamilyGroceryOrder
from app.services.family import FamilyError

_MAX_NAME = 200


def _now():
    return datetime.now(timezone.utc)


def _order_title(when=None) -> str:
    d = when or _now()
    return f"Order — {d.strftime('%b')} {d.day}"


# --------------------------------------------------------------------------
# the current order
# --------------------------------------------------------------------------

def current_order(*, member=None) -> FamilyGroceryOrder:
    """The one open order. Creates one (attributed to `member`, or m1) if
    none is open. If somehow more than one is open, keeps the newest and
    archives the rest."""
    opens = (
        FamilyGroceryOrder.query.filter_by(status="open")
        .order_by(FamilyGroceryOrder.created_at.desc())
        .all()
    )
    if opens:
        keep, extra = opens[0], opens[1:]
        for o in extra:
            o.status = "archived"
            o.archived_at = _now()
        if extra:
            db.session.commit()
        return keep

    from app.models import FamilyMember

    creator = member or FamilyMember.by_slug("m1")
    if creator is None:
        raise FamilyError("Family members are not seeded.")
    order = FamilyGroceryOrder(
        status="open", title=_order_title(), created_by_id=creator.id
    )
    db.session.add(order)
    db.session.commit()
    return order


# --------------------------------------------------------------------------
# items
# --------------------------------------------------------------------------

def add_item(data: dict, *, member) -> FamilyGroceryItem:
    name = (data.get("name") or "").strip()
    if not name:
        raise FamilyError("An item needs a name.")
    order = current_order(member=member)
    top = (
        db.session.query(db.func.max(FamilyGroceryItem.position))
        .filter_by(order_id=order.id)
        .scalar()
        or 0
    )
    item = FamilyGroceryItem(
        order_id=order.id,
        name=name[:_MAX_NAME],
        quantity=(data.get("quantity") or "").strip() or None,
        note=(data.get("note") or "").strip() or None,
        position=top + 1,
        added_by_id=member.id,
    )
    db.session.add(item)
    db.session.commit()
    return item


def _get_item(item_id: int) -> FamilyGroceryItem:
    item = db.session.get(FamilyGroceryItem, item_id)
    if item is None:
        raise FamilyError(f"No item with id {item_id}.")
    return item


def update_item(item_id: int, data: dict, *, member) -> FamilyGroceryItem:
    item = _get_item(item_id)
    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            raise FamilyError("An item needs a name.")
        item.name = name[:_MAX_NAME]
    if "quantity" in data:
        item.quantity = (data["quantity"] or "").strip() or None
    if "note" in data:
        item.note = (data["note"] or "").strip() or None
    db.session.commit()
    return item


def toggle_item(item_id: int) -> FamilyGroceryItem:
    item = _get_item(item_id)
    item.got = not item.got
    db.session.commit()
    return item


def remove_item(item_id: int) -> None:
    db.session.delete(_get_item(item_id))
    db.session.commit()


# --------------------------------------------------------------------------
# order lifecycle
# --------------------------------------------------------------------------

def _archive_open_orders() -> None:
    for o in FamilyGroceryOrder.query.filter_by(status="open").all():
        o.status = "archived"
        o.archived_at = _now()


def start_new_order(*, member) -> FamilyGroceryOrder:
    _archive_open_orders()
    order = FamilyGroceryOrder(
        status="open", title=_order_title(), created_by_id=member.id
    )
    db.session.add(order)
    db.session.commit()
    return order


def copy_order_forward(from_order_id: int, *, member) -> FamilyGroceryOrder:
    src = db.session.get(FamilyGroceryOrder, from_order_id)
    if src is None:
        raise FamilyError(f"No order with id {from_order_id}.")
    _archive_open_orders()
    order = FamilyGroceryOrder(
        status="open", title=_order_title(), created_by_id=member.id
    )
    db.session.add(order)
    db.session.flush()
    for pos, src_item in enumerate(
        src.items.order_by(FamilyGroceryItem.position).all(), start=1
    ):
        db.session.add(
            FamilyGroceryItem(
                order_id=order.id,
                name=src_item.name,
                quantity=src_item.quantity,
                note=src_item.note,
                got=False,
                position=pos,
                added_by_id=member.id,
            )
        )
    db.session.commit()
    return order


def archive_current(*, member) -> None:
    _archive_open_orders()
    db.session.commit()


def order_history(limit: int = 30) -> list[FamilyGroceryOrder]:
    return (
        FamilyGroceryOrder.query.filter_by(status="archived")
        .order_by(FamilyGroceryOrder.archived_at.desc(), FamilyGroceryOrder.id.desc())
        .limit(max(0, int(limit)))
        .all()
    )


def get_order(order_id: int) -> FamilyGroceryOrder:
    o = db.session.get(FamilyGroceryOrder, order_id)
    if o is None:
        raise FamilyError(f"No order with id {order_id}.")
    return o

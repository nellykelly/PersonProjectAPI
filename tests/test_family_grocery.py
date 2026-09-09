"""app/services/family/grocery.py -- order lifecycle + items."""
import pytest

from app import models
from app.extensions import db
from app.services.family import FamilyError
from app.services.family import grocery as g


@pytest.fixture()
def members(app):
    with app.app_context():
        db.create_all()
        n = models.FamilyMember(slug="m1", name="Nelson", accent="leaf")
        s = models.FamilyMember(slug="m2", name="Savannah", accent="bloom")
        db.session.add_all([n, s])
        db.session.commit()
        yield n, s


def _open_count():
    return models.FamilyGroceryOrder.query.filter_by(status="open").count()


def test_add_item_auto_creates_the_open_order(members):
    n, _ = members
    it = g.add_item({"name": "milk", "quantity": "2"}, member=n)
    assert _open_count() == 1 and it.added_by_id == n.id


def test_add_item_rejects_empty_name(members):
    n, _ = members
    with pytest.raises(FamilyError):
        g.add_item({"name": "  "}, member=n)


def test_toggle_item(members):
    n, _ = members
    it = g.add_item({"name": "eggs"}, member=n)
    assert g.toggle_item(it.id).got is True
    assert g.toggle_item(it.id).got is False


def test_start_new_order_archives_and_keeps_one_open(members):
    n, _ = members
    g.add_item({"name": "milk"}, member=n)
    new = g.start_new_order(member=n)
    assert _open_count() == 1
    assert new.items.count() == 0
    assert models.FamilyGroceryOrder.query.filter_by(status="archived").count() == 1


def test_copy_forward_reproduces_items_unticked(members):
    n, s = members
    g.add_item({"name": "milk", "quantity": "2"}, member=n)
    it = g.add_item({"name": "eggs"}, member=n)
    g.toggle_item(it.id)                       # tick one
    src_id = g.current_order().id
    copied = g.copy_order_forward(src_id, member=s)
    assert _open_count() == 1
    names = sorted(i.name for i in copied.items.all())
    assert names == ["eggs", "milk"]
    assert all(i.got is False for i in copied.items.all())
    assert [i.position for i in copied.items.order_by(models.FamilyGroceryItem.position).all()] == [1, 2]


def test_copy_forward_missing_order_raises(members):
    n, _ = members
    with pytest.raises(FamilyError):
        g.copy_order_forward(999, member=n)


def test_order_history_newest_first(members):
    n, _ = members
    g.add_item({"name": "a"}, member=n)
    g.start_new_order(member=n)
    g.add_item({"name": "b"}, member=n)
    g.start_new_order(member=n)
    hist = g.order_history()
    assert len(hist) == 2
    assert hist[0].archived_at >= hist[1].archived_at


def test_current_order_heals_multiple_opens(members):
    n, _ = members
    db.session.add_all([
        models.FamilyGroceryOrder(status="open", title="a", created_by_id=n.id),
        models.FamilyGroceryOrder(status="open", title="b", created_by_id=n.id),
    ])
    db.session.commit()
    assert _open_count() == 2
    g.current_order(member=n)
    assert _open_count() == 1


def test_grocery_route_flow(app, client):
    from werkzeug.security import generate_password_hash

    app.config["FAMILY_PASSWORD_HASH"] = generate_password_hash("pw")
    with app.app_context():
        db.create_all()
        db.session.add_all([
            models.FamilyMember(slug="m1", name="Nelson", accent="leaf"),
            models.FamilyMember(slug="m2", name="Savannah", accent="bloom"),
        ])
        db.session.commit()
    client.post("/family/unlock", data={"password": "pw"})
    assert b"groc-list" in client.get("/family/grocery").data
    assert client.post("/family/grocery/items", data={"name": "bananas"}).status_code == 302
    assert b"bananas" in client.get("/family/grocery").data
    assert client.post("/family/grocery/order/new").status_code == 302
    assert b"Past orders" in client.get("/family/grocery").data
    app.config["FAMILY_PASSWORD_HASH"] = None

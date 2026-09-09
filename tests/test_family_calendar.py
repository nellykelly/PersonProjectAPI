"""app/services/family/calendar.py -- CRUD, recurrence expansion, reads."""
from datetime import date

import pytest

from app import models
from app.extensions import db
from app.services.family import FamilyError
from app.services.family import calendar as cal


@pytest.fixture()
def members(app):
    with app.app_context():
        db.create_all()
        n = models.FamilyMember(slug="m1", name="Nelson", accent="leaf")
        s = models.FamilyMember(slug="m2", name="Savannah", accent="bloom")
        db.session.add_all([n, s])
        db.session.commit()
        yield n, s


def test_create_event_attributes_to_the_member(members):
    n, _ = members
    ev = cal.create_event({"title": "Dentist", "starts_on": "2026-09-15", "starts_at": "09:30"}, member=n)
    assert ev.id and ev.created_by_id == n.id
    assert ev.all_day is False


def test_create_requires_title_and_date(members):
    n, _ = members
    with pytest.raises(FamilyError):
        cal.create_event({"starts_on": "2026-09-15"}, member=n)
    with pytest.raises(FamilyError):
        cal.create_event({"title": "x"}, member=n)


def test_expand_one_off_in_and_out_of_window(members):
    n, _ = members
    ev = cal.create_event({"title": "x", "starts_on": "2026-09-15"}, member=n)
    assert cal.expand(ev, date(2026, 9, 1), date(2026, 9, 30)) == [date(2026, 9, 15)]
    assert cal.expand(ev, date(2026, 10, 1), date(2026, 10, 31)) == []


def test_weekly_recurrence_hits_every_matching_weekday(members):
    _, s = members
    ev = cal.create_event(
        {"title": "Bins", "starts_on": "2026-09-01", "recurrence": {"freq": "weekly", "byday": ["TU"]}},
        member=s,
    )
    got = cal.expand(ev, date(2026, 9, 1), date(2026, 9, 30))
    assert got == [date(2026, 9, d) for d in (1, 8, 15, 22, 29)]


def test_weekly_interval_two(members):
    n, _ = members
    ev = cal.create_event(
        {"title": "Gym", "starts_on": "2026-09-07",
         "recurrence": {"freq": "weekly", "interval": 2, "byday": ["MO", "FR"]}},
        member=n,
    )
    assert cal.expand(ev, date(2026, 9, 1), date(2026, 9, 30)) == [
        date(2026, 9, 7), date(2026, 9, 11), date(2026, 9, 21), date(2026, 9, 25)
    ]


def test_monthly_recurrence_clamps_short_months(members):
    n, _ = members
    ev = cal.create_event(
        {"title": "Bill", "starts_on": "2026-01-31", "recurrence": {"freq": "monthly"}},
        member=n,
    )
    got = cal.expand(ev, date(2026, 1, 1), date(2026, 4, 30))
    assert got == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]


def test_recurrence_until_is_inclusive(members):
    _, s = members
    ev = cal.create_event(
        {"title": "Class", "starts_on": "2026-09-02",
         "recurrence": {"freq": "weekly", "byday": ["WE"], "until": "2026-09-16"}},
        member=s,
    )
    assert cal.expand(ev, date(2026, 9, 1), date(2026, 12, 31)) == [
        date(2026, 9, 2), date(2026, 9, 9), date(2026, 9, 16)
    ]


def test_events_in_range_orders_by_date_then_time_then_title(members):
    n, _ = members
    cal.create_event({"title": "Dentist", "starts_on": "2026-09-15", "starts_at": "09:30"}, member=n)
    cal.create_event({"title": "Bins", "starts_on": "2026-09-15"}, member=n)  # all-day -> first
    rows = cal.events_in_range(date(2026, 9, 15), date(2026, 9, 15))
    assert [ev.title for _, ev in rows] == ["Bins", "Dentist"]


def test_upcoming_returns_the_next_n_in_order(members):
    n, _ = members
    cal.create_event({"title": "A", "starts_on": "2026-09-20"}, member=n)
    cal.create_event({"title": "B", "starts_on": "2026-09-10"}, member=n)
    rows = cal.upcoming(5, from_date=date(2026, 9, 1))
    assert [ev.title for _, ev in rows] == ["B", "A"]


def test_update_and_delete(members):
    n, _ = members
    ev = cal.create_event({"title": "x", "starts_on": "2026-09-15"}, member=n)
    cal.update_event(ev.id, {"title": "y"}, member=n)
    assert cal.get_event(ev.id).title == "y"
    cal.delete_event(ev.id)
    with pytest.raises(FamilyError):
        cal.get_event(ev.id)


def test_calendar_route_renders_and_adds(app, client):
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
    assert b"cal-grid" in client.get("/family/calendar").data
    r = client.post("/family/calendar/events", data={"title": "T", "starts_on": "2026-09-20", "all_day": "on"})
    assert r.status_code == 302
    assert b"T" in client.get("/family/calendar").data
    app.config["FAMILY_PASSWORD_HASH"] = None

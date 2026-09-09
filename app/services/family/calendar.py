"""The one writer + reader for the family calendar.

Events are one-off or recurring. Recurrence is a small dict on
``FamilyCalendarEvent.recurrence`` (null => one-off):

    {"freq": "weekly", "interval": 1, "byday": ["MO","WE"],
     "until": "2026-12-31", "count": null}

Only weekly and monthly are supported -- that covers the real cases (bin
day, the recycling, monthly bills). ``expand()`` is a pure function that
turns an event into its concrete occurrence dates inside a window.
"""
from __future__ import annotations

import calendar as _cal
from datetime import date, datetime, time, timedelta

from app.extensions import db
from app.models import FAMILY_RECURRENCE_FREQS, FamilyCalendarEvent
from app.services.family import FamilyError

_WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")   # Python weekday() order
_MAX_OCCURRENCES = 400   # hard stop so a bad rule can't spin forever


# --------------------------------------------------------------------------
# parsing / validation
# --------------------------------------------------------------------------

def _as_date(value, field: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        raise FamilyError(f"{field} needs to be a date like 2026-09-15.")


def _as_time(value, field: str):
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value
    raw = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    raise FamilyError(f"{field} needs to be a time like 09:30.")


def _clean_recurrence(rec) -> dict | None:
    if rec in (None, "", {}):
        return None
    if not isinstance(rec, dict):
        raise FamilyError("Recurrence must be a small object or left empty.")
    freq = str(rec.get("freq", "")).lower().strip()
    if freq not in FAMILY_RECURRENCE_FREQS:
        raise FamilyError("Recurrence 'freq' must be 'weekly' or 'monthly'.")
    out: dict = {"freq": freq}
    try:
        out["interval"] = max(1, int(rec.get("interval", 1)))
    except (TypeError, ValueError):
        raise FamilyError("Recurrence 'interval' must be a whole number.")
    if freq == "weekly":
        byday = rec.get("byday") or []
        if isinstance(byday, str):
            byday = [byday]
        days = [str(d).upper()[:2] for d in byday if str(d).upper()[:2] in _WEEKDAYS]
        out["byday"] = days   # empty => derived from the event's own weekday
    until = rec.get("until")
    out["until"] = _as_date(until, "Recurrence 'until'").isoformat() if until else None
    count = rec.get("count")
    out["count"] = max(1, int(count)) if count not in (None, "") else None
    return out


def _coerce(data: dict) -> dict:
    """Validate + normalise the subset of fields a create/update accepts."""
    out: dict = {}
    if "title" in data:
        title = (data["title"] or "").strip()
        if not title:
            raise FamilyError("An event needs a title.")
        out["title"] = title
    if "starts_on" in data:
        out["starts_on"] = _as_date(data["starts_on"], "The date")
    if "starts_at" in data:
        out["starts_at"] = _as_time(data["starts_at"], "The start time")
    if "ends_at" in data:
        out["ends_at"] = _as_time(data["ends_at"], "The end time")
    if "note" in data:
        note = (data["note"] or "").strip()
        out["note"] = note or None
    if "recurrence" in data:
        out["recurrence"] = _clean_recurrence(data["recurrence"])
    # all_day follows from whether a start time is set, unless given explicitly
    if "all_day" in data:
        out["all_day"] = bool(data["all_day"])
    elif "starts_at" in out:
        out["all_day"] = out["starts_at"] is None
    return out


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------

def create_event(data: dict, *, member) -> FamilyCalendarEvent:
    fields = _coerce(dict(data))
    if not fields.get("title"):
        raise FamilyError("An event needs a title.")
    if "starts_on" not in fields:
        raise FamilyError("An event needs a date.")
    fields.setdefault("all_day", fields.get("starts_at") is None)
    ev = FamilyCalendarEvent(created_by_id=member.id, **fields)
    db.session.add(ev)
    db.session.commit()
    return ev


def update_event(event_id: int, data: dict, *, member) -> FamilyCalendarEvent:
    ev = get_event(event_id)
    fields = _coerce(dict(data))
    for key, value in fields.items():
        setattr(ev, key, value)
    db.session.commit()
    return ev


def delete_event(event_id: int) -> None:
    ev = get_event(event_id)
    db.session.delete(ev)
    db.session.commit()


def get_event(event_id: int) -> FamilyCalendarEvent:
    ev = db.session.get(FamilyCalendarEvent, event_id)
    if ev is None:
        raise FamilyError(f"No event with id {event_id}.")
    return ev


# --------------------------------------------------------------------------
# recurrence expansion (pure)
# --------------------------------------------------------------------------

def expand(event: FamilyCalendarEvent, window_start: date, window_end: date) -> list[date]:
    """Every date `event` occurs on within [window_start, window_end]."""
    if window_end < window_start:
        return []
    rec = event.recurrence
    if not rec:
        return [event.starts_on] if window_start <= event.starts_on <= window_end else []

    freq = rec.get("freq")
    interval = max(1, int(rec.get("interval", 1)))
    until = date.fromisoformat(rec["until"]) if rec.get("until") else None
    hard_end = min(window_end, until) if until else window_end
    count_cap = rec.get("count")

    out: list[date] = []
    emitted = 0

    if freq == "weekly":
        byday = rec.get("byday") or [_WEEKDAYS[event.starts_on.weekday()]]
        targets = {_WEEKDAYS.index(d) for d in byday}
        # week 0 = the ISO week containing starts_on (Monday-anchored)
        anchor = event.starts_on - timedelta(days=event.starts_on.weekday())
        cur = window_start - timedelta(days=window_start.weekday())
        while cur <= hard_end and emitted < _MAX_OCCURRENCES:
            weeks_since = (cur - anchor).days // 7
            if weeks_since >= 0 and weeks_since % interval == 0:
                for wd in sorted(targets):
                    d = cur + timedelta(days=wd)
                    if d < event.starts_on or d < window_start or d > hard_end:
                        continue
                    out.append(d)
                    emitted += 1
                    if count_cap and emitted >= count_cap:
                        return sorted(out)
            cur += timedelta(days=7)
        return sorted(out)

    if freq == "monthly":
        dom = event.starts_on.day
        # step month by month from the event's own start month
        y, m = event.starts_on.year, event.starts_on.month
        step = 0
        while emitted < _MAX_OCCURRENCES:
            if step % interval == 0:
                last = _cal.monthrange(y, m)[1]
                d = date(y, m, min(dom, last))
                if d > hard_end:
                    break
                if d >= event.starts_on and d >= window_start:
                    out.append(d)
                    emitted += 1
                    if count_cap and emitted >= count_cap:
                        break
            # advance one month
            m += 1
            if m > 12:
                m = 1
                y += 1
            step += 1
            if date(y, m, 1) > hard_end and (not until or date(y, m, 1) > until):
                break
        return sorted(out)

    return []


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------

def _all_events() -> list[FamilyCalendarEvent]:
    return FamilyCalendarEvent.query.all()


def events_in_range(window_start: date, window_end: date):
    """[(date, event), ...] for every occurrence in the window, sorted by
    (date, start time or midnight, title)."""
    rows = []
    for ev in _all_events():
        for d in expand(ev, window_start, window_end):
            rows.append((d, ev))
    rows.sort(key=lambda p: (p[0], p[1].starts_at or time(0, 0), p[1].title.lower()))
    return rows


def upcoming(limit: int = 10, *, from_date: date | None = None):
    """The next `limit` occurrences from today (or `from_date`) forward."""
    start = from_date or date.today()
    window = 90
    for _ in range(4):   # extend a few times if the calendar is sparse
        rows = events_in_range(start, start + timedelta(days=window))
        if len(rows) >= limit or not _all_events():
            return rows[:limit]
        window *= 2
    return rows[:limit]

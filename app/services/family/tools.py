"""The tools Hera can call over the family's calendar, grocery list, and
memory.

`build_family_tools()` returns the schema list (the /family password gate
+ session is the access control -- checked at the route -- so there is no
per-call authorization branch here). `dispatch_family_tool(name, args, *,
member)` runs one call and **always returns a string**: a bad argument, an
unknown tool, any `FamilyError`, or any other exception all come back as
text for the model to relay. It never raises. Every write is attributed to
`member`.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from app.services.family import FamilyError
from app.services.family import calendar as cal
from app.services.family import grocery as groc
from app.services.family import memory as mem


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------

def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_DATE = {"type": "string", "description": "ISO date, YYYY-MM-DD."}
_TIME = {"type": "string", "description": "24-hour time, HH:MM."}

_TOOL_SPECS = [
    _tool(
        "add_event",
        "Add an event to the family calendar.",
        {
            "title": {"type": "string"},
            "date": _DATE,
            "time": {**_TIME, "description": "Start time; omit for an all-day event."},
            "end_time": _TIME,
            "note": {"type": "string"},
            "repeat": {"type": "string", "enum": ["weekly", "monthly"],
                       "description": "Omit for a one-off."},
            "repeat_weekdays": {
                "type": "array", "items": {"type": "string"},
                "description": "Two-letter weekday codes for a weekly repeat, e.g. ['MO','WE'].",
            },
            "repeat_until": {**_DATE, "description": "Last date the repeat runs."},
        },
        ["title", "date"],
    ),
    _tool(
        "list_events",
        "List calendar occurrences in a date range (default: the next 14 days). "
        "Returns event ids for use with update_event / delete_event.",
        {"from": _DATE, "to": _DATE},
        [],
    ),
    _tool(
        "update_event",
        "Change fields on an existing event (find its id with list_events first).",
        {
            "event_id": {"type": "integer"},
            "title": {"type": "string"},
            "date": _DATE,
            "time": _TIME,
            "end_time": _TIME,
            "note": {"type": "string"},
        },
        ["event_id"],
    ),
    _tool("delete_event", "Delete a calendar event by id.",
          {"event_id": {"type": "integer"}}, ["event_id"]),
    _tool(
        "add_grocery_item",
        "Add ONE item to the current open grocery order (created automatically if none is open).",
        {"name": {"type": "string"}, "quantity": {"type": "string"}, "note": {"type": "string"}},
        ["name"],
    ),
    _tool(
        "add_grocery_items",
        "Add MANY items to the current open grocery order in one call. Use this "
        "for a pasted or dictated list -- do not call add_grocery_item repeatedly.",
        {"items": {
            "type": "array",
            "description": "Each item: name (required), optional quantity, optional note.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["name"],
            },
        }},
        ["items"],
    ),
    _tool(
        "list_grocery",
        "List a grocery order. `which` = 'current' (default) or a past order's id. "
        "Returns item ids.",
        {"which": {"type": "string", "description": "'current' or a numeric order id."}},
        [],
    ),
    _tool("toggle_grocery_item", "Tick / untick a grocery item by id.",
          {"item_id": {"type": "integer"}}, ["item_id"]),
    _tool("remove_grocery_item", "Remove a grocery item by id.",
          {"item_id": {"type": "integer"}}, ["item_id"]),
    _tool("start_new_grocery_order",
          "Archive the current grocery order and open a fresh empty one.", {}, []),
    _tool("archive_grocery_order",
          "Save the current order to history (archive it) without opening a new one. "
          "The next added item will start a fresh order.", {}, []),
    _tool("copy_grocery_order_forward",
          "Archive the current order and open a new one pre-filled from a past order (by id), all items unticked.",
          {"from_order_id": {"type": "integer"}}, ["from_order_id"]),
    _tool("remember", "Store a short fact to keep across conversations.",
          {"text": {"type": "string"}}, ["text"]),
    _tool("forget", "Delete a stored memory by id.",
          {"memory_id": {"type": "integer"}}, ["memory_id"]),
    _tool("list_memories", "List stored memories with their ids.", {}, []),
]


def build_family_tools() -> list[dict]:
    return [json.loads(json.dumps(spec)) for spec in _TOOL_SPECS]


# --------------------------------------------------------------------------
# rendering helpers
# --------------------------------------------------------------------------

def _fmt_event_line(d: date, ev) -> str:
    when = d.isoformat()
    if ev.starts_at:
        when += f" {ev.starts_at.strftime('%H:%M')}"
    rep = " (repeats)" if ev.recurrence else ""
    return f"[{ev.id}] {when} — {ev.title}{rep}"


def _fmt_grocery(order) -> str:
    rows = order.items.all()
    if not rows:
        return f"Order {order.id} ({order.title}): empty."
    lines = [f"Order {order.id} ({order.title}):"]
    for it in rows:
        mark = "x" if it.got else " "
        qty = f" — {it.quantity}" if it.quantity else ""
        lines.append(f"  [{it.id}] ({mark}) {it.name}{qty}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# handlers -- each takes (args: dict, member) and returns a str
# --------------------------------------------------------------------------

def _add_event(a, member) -> str:
    data = {
        "title": a.get("title"),
        "starts_on": a.get("date"),
        "starts_at": a.get("time") or None,
        "ends_at": a.get("end_time") or None,
        "note": a.get("note"),
        "all_day": not a.get("time"),
    }
    rep = (a.get("repeat") or "").lower().strip()
    if rep in ("weekly", "monthly"):
        data["recurrence"] = {
            "freq": rep,
            "byday": a.get("repeat_weekdays") or [],
            "until": a.get("repeat_until") or None,
        }
    ev = cal.create_event(data, member=member)
    return f"Added event [{ev.id}]: {ev.title} on {ev.starts_on.isoformat()}" + (
        " (repeats)" if ev.recurrence else "."
    )


def _list_events(a, member) -> str:
    start = date.fromisoformat(a["from"]) if a.get("from") else date.today()
    end = date.fromisoformat(a["to"]) if a.get("to") else start + timedelta(days=14)
    rows = cal.events_in_range(start, end)
    if not rows:
        return f"No events between {start.isoformat()} and {end.isoformat()}."
    return "\n".join(_fmt_event_line(d, ev) for d, ev in rows)


def _update_event(a, member) -> str:
    data = {}
    for src, dst in (("title", "title"), ("date", "starts_on"),
                     ("time", "starts_at"), ("end_time", "ends_at"), ("note", "note")):
        if src in a and a[src] not in (None, ""):
            data[dst] = a[src]
    if not data:
        return "Nothing to change — say which field."
    ev = cal.update_event(int(a["event_id"]), data, member=member)
    return f"Updated event [{ev.id}]: {ev.title} on {ev.starts_on.isoformat()}."


def _delete_event(a, member) -> str:
    cal.delete_event(int(a["event_id"]))
    return f"Deleted event [{a['event_id']}]."


def _add_grocery_item(a, member) -> str:
    it = groc.add_item(
        {"name": a.get("name"), "quantity": a.get("quantity"), "note": a.get("note")},
        member=member,
    )
    return f"Added to the list: [{it.id}] {it.name}" + (
        f" ({it.quantity})" if it.quantity else "."
    )


def _add_grocery_items(a, member) -> str:
    items = a.get("items")
    if not isinstance(items, list) or not items:
        return "`items` must be a non-empty list of {name, quantity?, note?}."
    added, skipped = [], 0
    for raw in items[:200]:
        name = (raw.get("name") if isinstance(raw, dict) else str(raw)) or ""
        if not name.strip():
            skipped += 1
            continue
        try:
            it = groc.add_item(
                {
                    "name": name,
                    "quantity": raw.get("quantity") if isinstance(raw, dict) else None,
                    "note": raw.get("note") if isinstance(raw, dict) else None,
                },
                member=member,
            )
            added.append(it.name)
        except FamilyError:
            skipped += 1
    tail = f" ({skipped} skipped)" if skipped else ""
    return f"Added {len(added)} item(s) to the list{tail}: " + ", ".join(added[:40]) + (
        " …" if len(added) > 40 else ""
    )


def _list_grocery(a, member) -> str:
    which = str(a.get("which") or "current").strip().lower()
    if which in ("", "current"):
        return _fmt_grocery(groc.current_order(member=member))
    try:
        order = groc.get_order(int(which))
    except (ValueError, TypeError):
        return "`which` must be 'current' or a numeric order id."
    return _fmt_grocery(order)


def _toggle_grocery_item(a, member) -> str:
    it = groc.toggle_item(int(a["item_id"]))
    return f"[{it.id}] {it.name} is now {'got' if it.got else 'not got'}."


def _remove_grocery_item(a, member) -> str:
    groc.remove_item(int(a["item_id"]))
    return f"Removed item [{a['item_id']}]."


def _start_new_grocery_order(a, member) -> str:
    o = groc.start_new_order(member=member)
    return f"Started a fresh order [{o.id}]. The previous one is archived."


def _archive_grocery_order(a, member) -> str:
    current = groc.current_order(member=member)
    oid, n = current.id, current.items.count()
    groc.archive_current(member=member)
    return f"Order [{oid}] ({n} item(s)) saved to history. The next item starts a new order."


def _copy_grocery_order_forward(a, member) -> str:
    o = groc.copy_order_forward(int(a["from_order_id"]), member=member)
    return f"New order [{o.id}] copied from order {a['from_order_id']} — {o.items.count()} items, all unticked."


def _remember(a, member) -> str:
    row = mem.save(a.get("text") or "", member=member)
    return f"Noted [{row.id}]: {row.content}"


def _forget(a, member) -> str:
    mem.forget(int(a["memory_id"]))
    return f"Forgotten [{a['memory_id']}]."


def _list_memories(a, member) -> str:
    rows = mem.list_all()
    if not rows:
        return "No memories stored."
    return "\n".join(f"[{r.id}] {r.content}" for r in rows)


_HANDLERS = {
    "add_event": _add_event,
    "list_events": _list_events,
    "update_event": _update_event,
    "delete_event": _delete_event,
    "add_grocery_item": _add_grocery_item,
    "add_grocery_items": _add_grocery_items,
    "list_grocery": _list_grocery,
    "toggle_grocery_item": _toggle_grocery_item,
    "remove_grocery_item": _remove_grocery_item,
    "start_new_grocery_order": _start_new_grocery_order,
    "archive_grocery_order": _archive_grocery_order,
    "copy_grocery_order_forward": _copy_grocery_order_forward,
    "remember": _remember,
    "forget": _forget,
    "list_memories": _list_memories,
}


def dispatch_family_tool(name: str, arguments, *, member) -> str:
    """Run one tool call. Always returns a string; never raises."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool {name!r}."

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except Exception:  # noqa: BLE001 - ValueError, RecursionError on deep nesting
            return f"Could not parse arguments for {name!r}."
    if not isinstance(arguments, dict):
        arguments = {}

    try:
        return handler(arguments, member)
    except FamilyError as exc:
        return str(exc)
    except (KeyError, ValueError, TypeError) as exc:
        return f"That call was missing or malformed ({type(exc).__name__})."
    except Exception as exc:  # noqa: BLE001 - the model must never see a trace
        return f"That didn't work ({type(exc).__name__})."

"""The job-tracker tools Hera may call -- schemas plus a dispatcher.

Offered to the model only when `app.services.assistant.authz.can_use_job_tools()`
is true (the owner is signed in *and* the /job-tracker gate is unlocked). The
orchestrator builds the schemas with `build_job_tools(authorized)` -- which
returns `[]` for anyone else, so there is nothing for a crafted message or a
retrieved passage to invoke -- and runs each requested call through
`dispatch_job_tool(...)`.

Every write goes through `app.services.job_tracker` with `source="assistant"`,
so it lands in the same `JobApplicationEvent` audit log as a web-UI edit,
tagged as the assistant's doing. This module never touches `db.session` and
never raises out of `dispatch_job_tool`: a bad argument, an unknown status, a
missing or ambiguous record all come back as a short string for the model to
read and relay. Deletion is deliberately not a tool -- it stays in the web UI.
"""
from __future__ import annotations

import json
from typing import Any

from app.models import JOB_APPLICATION_STATUSES
from app.services import job_tracker

_STATUS_LIST = list(JOB_APPLICATION_STATUSES)
_MAX_LIST_ROWS = 60


# --------------------------------------------------------------------------
# Schemas
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


_COMPANY = {"type": "string", "description": "Company name (substring match is fine)."}
_ROLE_LOCATOR = {
    "type": "string",
    "description": "Role title, needed only to disambiguate when there is more "
    "than one application at the same company.",
}
_STATUS = {
    "type": "string",
    "enum": _STATUS_LIST,
    "description": "One of the fixed pipeline stages.",
}

_TOOL_SPECS = [
    _tool(
        "add_application",
        "Add a new job application to the owner's private tracker.",
        {
            "company": {"type": "string", "description": "Company name."},
            "role": {"type": "string", "description": "Role / job title."},
            "status": {
                **_STATUS,
                "description": "Pipeline stage; defaults to 'Applied' if omitted.",
            },
            "job_posting_url": {"type": "string", "description": "Link to the posting."},
            "date_applied": {
                "type": "string",
                "description": "ISO date YYYY-MM-DD; defaults to today if omitted.",
            },
            "notes": {"type": "string", "description": "Free-text notes."},
        },
        ["company", "role"],
    ),
    _tool(
        "update_application",
        "Change fields on an existing application (found by company, plus role "
        "if the company has more than one). Only the fields you pass are touched.",
        {
            "company": _COMPANY,
            "role": _ROLE_LOCATOR,
            "status": _STATUS,
            "job_posting_url": {"type": "string"},
            "date_applied": {"type": "string", "description": "ISO date YYYY-MM-DD."},
            "salary_range": {"type": "string"},
            "location_remote_policy": {"type": "string"},
            "company_industry": {"type": "string"},
            "notes": {"type": "string"},
        },
        ["company"],
    ),
    _tool(
        "set_application_status",
        "Move an existing application to a new pipeline stage.",
        {"company": _COMPANY, "role": _ROLE_LOCATOR, "status": _STATUS},
        ["company", "status"],
    ),
    _tool(
        "list_applications",
        "List applications in the tracker, optionally filtered to one status.",
        {"status": _STATUS},
        [],
    ),
    _tool(
        "find_application",
        "Look up a single application and return its full detail.",
        {"company": _COMPANY, "role": _ROLE_LOCATOR},
        ["company"],
    ),
    _tool(
        "ghost_stale_applications",
        "Move every application that has sat in 'Applied' past the staleness "
        "threshold (default 2.5 weeks) to 'Ghosted'. Use when Nelson asks to "
        "tidy up or ghost old applications.",
        {
            "weeks": {
                "type": "number",
                "description": "Override the age threshold in weeks; omit for the default.",
            }
        },
        [],
    ),
]


def build_job_tools(authorized: bool) -> list[dict]:
    """The tool schemas to hand the model -- or `[]` when the caller is not
    the signed-in, unlocked owner. This is the single chokepoint: no
    schemas, nothing to call."""
    if not authorized:
        return []
    # Fresh copy each call -- callers must not mutate the module list.
    return [json.loads(json.dumps(spec)) for spec in _TOOL_SPECS]


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def _render_row(d: dict) -> str:
    bits = [f"{d['company_name']} — {d['role_title']}", d["status"]]
    if d.get("date_applied"):
        bits.append(f"applied {d['date_applied']}")
    return " · ".join(bits) + f"  (id {d['id']})"


def _render_detail(d: dict) -> str:
    lines = [f"{d['company_name']} — {d['role_title']}  (id {d['id']})",
             f"  status: {d['status']}"]
    for key, label in (
        ("date_applied", "applied"),
        ("job_posting_url", "posting"),
        ("salary_range", "salary"),
        ("location_remote_policy", "location"),
        ("company_industry", "industry"),
        ("match_label", "match"),
        ("notes", "notes"),
    ):
        val = d.get(key)
        if val:
            lines.append(f"  {label}: {val}")
    return "\n".join(lines)


def _locate(args: dict) -> job_tracker.JobApplication:
    """Resolve the company/role locator to exactly one application, or raise
    JobTrackerError with a model-readable message."""
    company = (args.get("company") or "").strip()
    role = (args.get("role") or "").strip() or None
    if not company:
        raise job_tracker.JobTrackerError("Which company? I need a name to look it up.")
    match = job_tracker.find_application(company, role)
    if match is None:
        where = f"'{company}'" + (f" / '{role}'" if role else "")
        raise job_tracker.JobTrackerError(f"No application matches {where}.")
    return match


def _add_application(args: dict) -> str:
    data = {
        "company_name": (args.get("company") or "").strip(),
        "role_title": (args.get("role") or "").strip(),
    }
    for key in ("status", "job_posting_url", "date_applied", "notes"):
        if args.get(key) not in (None, ""):
            data[key] = args[key]
    app = job_tracker.create_application(data, source="assistant")
    d = job_tracker.application_as_dict(app)
    return f"Added {d['company_name']} — {d['role_title']} ({d['status']}), id {d['id']}."


_UPDATE_FIELDS = (
    "status",
    "job_posting_url",
    "date_applied",
    "salary_range",
    "location_remote_policy",
    "company_industry",
    "notes",
)


def _update_application(args: dict) -> str:
    app = _locate(args)
    data = {k: args[k] for k in _UPDATE_FIELDS if args.get(k) not in (None, "")}
    if not data:
        return (
            f"{app.company_name} — {app.role_title}: nothing to change. Tell me "
            f"which field (status, notes, salary_range, …)."
        )
    updated = job_tracker.update_application(app.id, data, source="assistant")
    changed = ", ".join(sorted(data))
    return f"Updated {updated.company_name} — {updated.role_title}: {changed}."


def _set_application_status(args: dict) -> str:
    status = (args.get("status") or "").strip()
    if not status:
        raise job_tracker.JobTrackerError("Which status? One of: " + ", ".join(_STATUS_LIST))
    app = _locate(args)
    updated = job_tracker.set_status(app.id, status, source="assistant")
    return f"Moved {updated.company_name} — {updated.role_title} to {updated.status}."


def _list_applications(args: dict) -> str:
    status = (args.get("status") or "").strip() or None
    rows = job_tracker.list_applications(status=status)
    if not rows:
        return "No applications" + (f" with status {status}." if status else " tracked yet.")
    head = f"{len(rows)} application(s)" + (f" with status {status}" if status else "") + ":"
    shown = rows[:_MAX_LIST_ROWS]
    body = "\n".join(
        "- " + _render_row(job_tracker.application_as_dict(r)) for r in shown
    )
    if len(rows) > _MAX_LIST_ROWS:
        body += f"\n… and {len(rows) - _MAX_LIST_ROWS} more."
    return f"{head}\n{body}"


def _find_application(args: dict) -> str:
    app = _locate(args)
    return _render_detail(job_tracker.application_as_dict(app))


def _ghost_stale_applications(args: dict) -> str:
    from flask import current_app

    weeks = args.get("weeks")
    if weeks in (None, ""):
        weeks = current_app.config.get("JOB_TRACKER_GHOST_AFTER_WEEKS", 2.5)
    try:
        weeks = float(weeks)
    except (TypeError, ValueError):
        return "The 'weeks' value needs to be a number."
    moved = job_tracker.sweep_stale_applications(weeks=weeks, source="assistant")
    if not moved:
        return f"Nothing has been sitting in 'Applied' for {weeks:g} weeks."
    lines = "\n".join(f"- {m.company_name} — {m.role_title}" for m in moved)
    return f"Moved {len(moved)} stale application(s) to Ghosted:\n{lines}"


_HANDLERS = {
    "add_application": _add_application,
    "update_application": _update_application,
    "set_application_status": _set_application_status,
    "list_applications": _list_applications,
    "find_application": _find_application,
    "ghost_stale_applications": _ghost_stale_applications,
}


def dispatch_job_tool(name: str, arguments: Any, *, authorized: bool) -> str:
    """Execute one tool call and return a short string for the model, always.

    Never raises: an unknown tool, a bad argument type, a `JobTrackerError`
    from the service, or any unexpected exception all come back as text.
    Re-checks `authorized` even though the orchestrator only reaches here on
    the authorized path -- defence in depth.
    """
    if not authorized:
        return "The job tracker isn't available on this request."

    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool {name!r}."

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except Exception:  # noqa: BLE001 - ValueError, or RecursionError on pathological nesting
            return f"Could not parse arguments for {name!r}."
    if not isinstance(arguments, dict):
        arguments = {}

    try:
        return handler(arguments)
    except job_tracker.JobTrackerError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - the model must never see a trace
        return f"That didn't work ({type(exc).__name__})."

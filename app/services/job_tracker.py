"""The one implementation of every job-tracker read and write.

Both the gated `/job-tracker` blueprint and (later) the personal AI
assistant's admin-only tools go through this module -- so the assistant
can't reach the data by a path the blueprint's access checks don't cover,
and every write lands in the JobApplicationEvent audit log the same way
regardless of who made it.

Nothing here does access control. The blueprint enforces the password
gate; the assistant enforces the admin-session check. This module trusts
its caller and just records *which* caller it was, via the `source`
argument ("web" / "assistant" / "cli").

Plain SQLAlchemy ORM against portable column types -- works identically on
SQLite (the app default) and Postgres.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

from app.extensions import db
from app.models import (
    JOB_APPLICATION_ACTIVE_STATUSES,
    JOB_APPLICATION_RESPONDED_STATUSES,
    JOB_APPLICATION_STATUSES,
    JobApplication,
    JobApplicationEvent,
    utcnow,
)

STATUSES: tuple[str, ...] = JOB_APPLICATION_STATUSES

# Fields a caller is allowed to set through create/update. `status` is
# handled specially (it bumps status_updated_at), so it's listed here for
# create but routed through set_status() on an update.
_EDITABLE_FIELDS = (
    "company_name",
    "role_title",
    "job_posting_url",
    "date_applied",
    "status",
    "source",
    "resume_version",
    "cover_letter_used",
    "company_industry",
    "company_size_stage",
    "location_remote_policy",
    "tech_stack",
    "salary_range",
    "match_grade",
    "match_notes",
    "notes",
)

_VALID_SOURCES = ("web", "assistant", "cli")

# Columns the dashboard's ?sort= is allowed to order by. A plain
# getattr(JobApplication, name) is not enough on its own -- it also
# resolves relationship names ("events") and @property names
# ("tech_stack_list"), which then raise inside order_by() rather than
# sorting. Anything not in this set falls back to the default order.
_SORTABLE = frozenset(
    {
        "id",
        "company_name",
        "role_title",
        "status",
        "date_applied",
        "status_updated_at",
        "match_grade",
        "created_at",
        "updated_at",
    }
)


class JobTrackerError(ValueError):
    """Bad input to a job-tracker operation (unknown status, out-of-range
    match grade, missing required field, unknown application id)."""


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------


def match_label(grade: int | None) -> str | None:
    """The word that goes next to the number (spec's rubric)."""
    if grade is None:
        return None
    if grade >= 90:
        return "Strong match"
    if grade >= 75:
        return "Good match"
    if grade >= 60:
        return "Worth applying, real gaps"
    return "Reach"


def list_applications(
    *,
    status: str | None = None,
    search: str | None = None,
    sort: str = "-date_applied",
) -> list[JobApplication]:
    """All applications, newest-applied first by default.

    `status` filters to one status. `search` matches company or role
    (case-insensitive substring). `sort` is a column name, optionally
    "-"-prefixed for descending; unknown values fall back to the default.
    """
    query = JobApplication.query
    if status:
        if status not in STATUSES:
            raise JobTrackerError(f"Unknown status {status!r}.")
        query = query.filter(JobApplication.status == status)
    if search:
        like = f"%{search.strip()}%"
        query = query.filter(
            db.or_(
                JobApplication.company_name.ilike(like),
                JobApplication.role_title.ilike(like),
            )
        )

    descending = sort.startswith("-")
    column_name = sort[1:] if descending else sort
    if column_name not in _SORTABLE:
        column_name, descending = "date_applied", True
    column = getattr(JobApplication, column_name)
    # NULLs last regardless of direction, then the chosen order, then id
    # as a stable tie-breaker.
    query = query.order_by(
        (column.is_(None)).asc(),
        column.desc() if descending else column.asc(),
        JobApplication.id.desc(),
    )
    return query.all()


def get_application(app_id: int) -> JobApplication:
    row = db.session.get(JobApplication, app_id)
    if row is None:
        raise JobTrackerError(f"No application with id {app_id}.")
    return row


def summary_stats() -> dict[str, Any]:
    """Top-of-dashboard counters: total, active interviews, offers, and an
    overall response rate (share of applications that got past "Applied"
    without being ghosted)."""
    rows = JobApplication.query.all()
    total = len(rows)
    active = sum(1 for r in rows if r.status in JOB_APPLICATION_ACTIVE_STATUSES)
    offers = sum(1 for r in rows if r.status == "Offer")
    responded = sum(1 for r in rows if r.status in JOB_APPLICATION_RESPONDED_STATUSES)
    response_rate = round(responded / total * 100) if total else 0
    return {
        "total": total,
        "active_interviews": active,
        "offers": offers,
        "responded": responded,
        "response_rate": response_rate,
    }


def status_counts() -> dict[str, int]:
    """{status: count} across every status, including the zeros -- so the
    dashboard's status breakdown always renders every column."""
    counts = {s: 0 for s in STATUSES}
    for row in JobApplication.query.with_entities(JobApplication.status).all():
        if row.status in counts:
            counts[row.status] += 1
    return counts


def recent_events(limit: int = 100) -> list[JobApplicationEvent]:
    return (
        JobApplicationEvent.query.order_by(JobApplicationEvent.created_at.desc())
        .limit(limit)
        .all()
    )


# --------------------------------------------------------------------------
# writes -- every one records a JobApplicationEvent
# --------------------------------------------------------------------------


def _check_source(source: str) -> None:
    if source not in _VALID_SOURCES:
        raise JobTrackerError(f"source must be one of {_VALID_SOURCES}, got {source!r}.")


def _coerce(field: str, value: Any) -> Any:
    """Normalise one incoming field value to what the column expects.
    Raises JobTrackerError on anything it can't make sense of."""
    if value is None:
        return None

    if field == "status":
        if value not in STATUSES:
            raise JobTrackerError(
                f"Unknown status {value!r}. One of: {', '.join(STATUSES)}."
            )
        return value

    if field == "date_applied":
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        text = str(value).strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise JobTrackerError(
                f"date_applied must be YYYY-MM-DD, got {value!r}."
            ) from exc

    if field == "cover_letter_used":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    if field == "match_grade":
        text = str(value).strip()
        if text == "":
            return None
        try:
            grade = int(text)
        except ValueError as exc:
            raise JobTrackerError(f"match_grade must be a whole number, got {value!r}.") from exc
        if not 0 <= grade <= 100:
            raise JobTrackerError(f"match_grade must be 0-100, got {grade}.")
        return grade

    # Everything else is free text. Blank string -> NULL so the column is
    # cleanly empty rather than holding "".
    text = str(value).strip()
    return text or None


def _display(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def create_application(data: dict[str, Any], *, source: str = "web") -> JobApplication:
    _check_source(source)

    company = _coerce("company_name", data.get("company_name"))
    role = _coerce("role_title", data.get("role_title"))
    if not company or not role:
        raise JobTrackerError("company_name and role_title are required.")

    app = JobApplication()
    app.company_name = company
    app.role_title = role

    for field in _EDITABLE_FIELDS:
        if field in ("company_name", "role_title"):
            continue
        if field not in data:
            continue
        value = _coerce(field, data.get(field))
        if field == "tech_stack":
            app.set_tech_stack(value or "")
        else:
            setattr(app, field, value)

    if app.status is None:
        app.status = "Applied"
    app.status_updated_at = utcnow()

    db.session.add(app)
    db.session.flush()  # assign app.id before writing the event

    db.session.add(
        JobApplicationEvent(
            application_id=app.id,
            action="create",
            source=source,
            summary=f"Added {app.company_name} - {app.role_title} ({app.status})",
        )
    )
    db.session.commit()
    return app


def update_application(
    app_id: int, data: dict[str, Any], *, source: str = "web"
) -> JobApplication:
    """Apply a partial update. Only keys present in `data` are touched.
    Writes one audit event per field that actually changed value."""
    _check_source(source)
    app = get_application(app_id)

    changes: list[tuple[str, Any, Any]] = []

    for field in _EDITABLE_FIELDS:
        if field not in data:
            continue

        if field == "status":
            new_value = _coerce("status", data["status"])
            if new_value != app.status:
                changes.append(("status", app.status, new_value))
                app.status = new_value
                app.status_updated_at = utcnow()
            continue

        if field == "tech_stack":
            old_value = app.tech_stack
            app.set_tech_stack(_coerce("tech_stack", data["tech_stack"]) or "")
            if app.tech_stack != old_value:
                changes.append(("tech_stack", old_value, app.tech_stack))
            continue

        new_value = _coerce(field, data[field])
        old_value = getattr(app, field)
        if new_value != old_value:
            changes.append((field, old_value, new_value))
            setattr(app, field, new_value)

    for field, old_value, new_value in changes:
        db.session.add(
            JobApplicationEvent(
                application_id=app.id,
                action="update",
                source=source,
                field_name=field,
                old_value=_display(old_value),
                new_value=_display(new_value),
            )
        )

    if changes:
        db.session.commit()
    else:
        db.session.rollback()
    return app


def set_status(app_id: int, new_status: str, *, source: str = "web") -> JobApplication:
    """One-field status change -- the dashboard's inline <select> and the
    assistant's update_application_status tool both land here."""
    return update_application(app_id, {"status": new_status}, source=source)


def delete_application(app_id: int, *, source: str = "web") -> None:
    _check_source(source)
    app = get_application(app_id)
    label = f"{app.company_name} - {app.role_title}"
    # Detach the audit history so it survives the delete (application_id
    # is nullable for exactly this).
    JobApplicationEvent.query.filter_by(application_id=app.id).update(
        {"application_id": None}
    )
    db.session.add(
        JobApplicationEvent(
            application_id=None,
            action="delete",
            source=source,
            summary=f"Deleted {label}",
        )
    )
    db.session.delete(app)
    db.session.commit()


def application_as_dict(app: JobApplication) -> dict[str, Any]:
    """Flat, JSON-friendly view -- what the assistant's read tools will
    hand back, and handy in tests."""
    return {
        "id": app.id,
        "company_name": app.company_name,
        "role_title": app.role_title,
        "job_posting_url": app.job_posting_url,
        "date_applied": _display(app.date_applied),
        "status": app.status,
        "status_updated_at": _display(app.status_updated_at),
        "source": app.source,
        "resume_version": app.resume_version,
        "cover_letter_used": app.cover_letter_used,
        "company_industry": app.company_industry,
        "company_size_stage": app.company_size_stage,
        "location_remote_policy": app.location_remote_policy,
        "tech_stack": app.tech_stack_list,
        "salary_range": app.salary_range,
        "match_grade": app.match_grade,
        "match_label": match_label(app.match_grade),
        "match_notes": app.match_notes,
        "notes": app.notes,
    }


def find_application(company: str, role: str | None = None) -> JobApplication | None:
    """Case-insensitive lookup by company (and optionally role) -- the
    shape the assistant's tools need, since they get names, not ids.
    Returns None if there's no match; raises JobTrackerError if the match
    is ambiguous (so a tool doesn't silently edit the wrong row)."""
    like_company = f"%{company.strip()}%"
    query = JobApplication.query.filter(JobApplication.company_name.ilike(like_company))
    if role:
        query = query.filter(JobApplication.role_title.ilike(f"%{role.strip()}%"))
    matches = query.all()
    if not matches:
        return None
    if len(matches) > 1:
        raise JobTrackerError(
            f"{len(matches)} applications match company={company!r}"
            + (f" role={role!r}" if role else "")
            + " -- be more specific."
        )
    return matches[0]


def iter_statuses() -> Iterable[str]:
    return iter(STATUSES)

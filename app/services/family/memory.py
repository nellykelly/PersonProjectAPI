"""Shared long-term memory for the Hera chatbot.

A flat table of one-line facts the household (or Hera herself) wants kept
across conversations -- "bin day is Tuesday", "Sav's mum visits the first
weekend of the month". Not per-member: both threads see the same
memories. No embeddings; at family scale it is tens of rows, loaded whole
into the prompt (capped at FAMILY_MEMORY_LIMIT, newest first).
"""
from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.models import FamilyMemory
from app.services.family import FamilyError

_MAX_CONTENT = 500


def save(text: str, *, member=None) -> FamilyMemory:
    """Store one fact. `member` None => Hera wrote it herself."""
    content = (text or "").strip()
    if not content:
        raise FamilyError("A memory can't be empty.")
    row = FamilyMemory(
        content=content[:_MAX_CONTENT],
        created_by_id=member.id if member is not None else None,
    )
    db.session.add(row)
    db.session.commit()
    return row


def list_all(limit: int | None = None) -> list[FamilyMemory]:
    """Newest first. `limit` None => everything."""
    q = FamilyMemory.query.order_by(FamilyMemory.created_at.desc())
    if limit is not None:
        q = q.limit(max(0, int(limit)))
    return q.all()


def forget(memory_id: int) -> None:
    row = db.session.get(FamilyMemory, memory_id)
    if row is None:
        raise FamilyError(f"No memory with id {memory_id}.")
    db.session.delete(row)
    db.session.commit()


def for_prompt() -> str:
    """The block injected into Hera's system prompt: up to
    FAMILY_MEMORY_LIMIT newest memories, one per line. Empty string if
    there are none."""
    limit = int(current_app.config.get("FAMILY_MEMORY_LIMIT", 60))
    rows = list_all(limit=limit)
    if not rows:
        return ""
    return "\n".join(f"- {r.content}" for r in rows)

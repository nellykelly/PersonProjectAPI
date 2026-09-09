"""The Hera chat turn for /family/chat.

Reuses the assistant's LLM plumbing without forking it: the pure
`backends.py` classes and the (parameterised) `_run_tool_loop`. It brings
its own backend instance (a separate Groq key), its own system prompt, its
own tools + dispatcher, and its own persistence -- per-member threads in
`family_chat_messages`. No retrieval, no embeddings, no corpus.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.extensions import db
from app.models import FamilyChatMessage
from app.services.assistant.backends import (
    FakeBackend,
    GroqBackend,
    LLMReply,  # noqa: F401  (re-exported for tests)
    SCRIPTED_BACKEND,
)
from app.services.assistant.errors import AssistantInputError, AssistantUnavailable
from app.services.assistant.orchestrator import _run_tool_loop
from app.services.family import memory as mem_svc
from app.services.family import persona
from app.services.family.tools import build_family_tools, dispatch_family_tool

# Family-local errors reuse the assistant's classes so the route's
# existing except-blocks style carries over.
FamilyChatInputError = AssistantInputError
FamilyChatUnavailable = AssistantUnavailable

_ALLOWED_REPLAY_ROLES = {"user", "assistant"}
_TOOL_CALL_ID_MAX = 64

_FAMILY_CACHE: dict[tuple, object] = {}


@dataclass
class FamilyReply:
    reply: str
    model: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def build_family_backend(config):
    """The family bot's own backend. Not `assistant.build_backend` -- that
    reads GROQ_API_KEY hard and its cache ignores the key, so a shared
    call could hand back the wrong client."""
    kind = (config.get("FAMILY_LLM_BACKEND") or "groq").strip()
    if kind == "fake":
        return FakeBackend()
    if kind == "scripted":
        return SCRIPTED_BACKEND
    if kind == "groq":
        key = config.get("FAMILY_GROQ_API_KEY") or ""
        if not key:
            raise FamilyChatUnavailable("FAMILY_GROQ_API_KEY is not configured")
        model = config["FAMILY_GROQ_MODEL"]
        ck = ("family-groq", model)
        if ck not in _FAMILY_CACHE:
            _FAMILY_CACHE[ck] = GroqBackend(key, model)
        return _FAMILY_CACHE[ck]
    raise FamilyChatUnavailable(f"unknown FAMILY_LLM_BACKEND {kind!r}")


def _load_history(member_id: int, max_turns: int) -> list[dict]:
    rows = (
        FamilyChatMessage.query.filter_by(member_id=member_id)
        .order_by(FamilyChatMessage.created_at.desc(), FamilyChatMessage.id.desc())
        .limit(max_turns * 2)
        .all()
    )
    out = []
    for r in reversed(rows):
        if r.role in _ALLOWED_REPLAY_ROLES and r.content:
            out.append({"role": r.role, "content": r.content})
    return out


def answer(member, message: str, *, config) -> FamilyReply:
    """Run one turn for `member`. Persists the user turn and the reply."""
    text = (message or "").strip()
    if not text:
        raise FamilyChatInputError("Say something first.")
    max_chars = int(config.get("FAMILY_MAX_INPUT_CHARS", 2000))
    if len(text) > max_chars:
        raise FamilyChatInputError(f"That's a bit long (limit {max_chars} characters).")

    max_turns = int(config.get("FAMILY_MAX_HISTORY_TURNS", 8))
    max_tokens = int(config.get("FAMILY_MAX_OUTPUT_TOKENS", 700))

    history = _load_history(member.id, max_turns)
    system = persona.system_prompt(member, mem_svc.for_prompt())
    messages = (
        [{"role": "system", "content": system}]
        + history
        + [{"role": "user", "content": text}]
    )

    backend = build_family_backend(config)
    final_text, p_tok, c_tok, model = _run_tool_loop(
        backend,
        messages,
        build_family_tools(),
        max_tokens,
        dispatch=lambda n, a: dispatch_family_tool(n, a, member=member),
        max_iters=6,   # a pasted list -> batch add -> read back -> archive, with headroom
        fallback=(
            "That was a lot at once and I didn't get all the way through. "
            "Tell me to keep going, or add the rest on the Grocery screen."
        ),
    )
    final_text = (final_text or "").strip()

    db.session.add(FamilyChatMessage(member_id=member.id, role="user", content=text))
    db.session.add(
        FamilyChatMessage(member_id=member.id, role="assistant", content=final_text)
    )
    db.session.commit()

    return FamilyReply(
        reply=final_text, model=model, prompt_tokens=p_tok, completion_tokens=c_tok
    )

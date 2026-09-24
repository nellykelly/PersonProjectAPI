"""Personal AI assistant: RAG-grounded chat over the site's own content.

Public surface:
    answer(question, history, *, config, is_admin=False) -> AssistantAnswer
    reindex(*, root=None) -> dict
    backend_available(config) -> bool
    AssistantError / AssistantInputError / AssistantUnavailable

Everything heavy (groq, fastembed, pgvector) is imported lazily inside the
submodules, so importing this package -- and booting the app, and running
the test suite -- works without them installed.
"""
from .backends import backend_available
from .errors import AssistantBusy, AssistantError, AssistantInputError, AssistantUnavailable
from .orchestrator import AssistantAnswer, answer, stream_answer
from .reindex import reindex

__all__ = [
    "answer",
    "stream_answer",
    "reindex",
    "backend_available",
    "AssistantAnswer",
    "AssistantError",
    "AssistantInputError",
    "AssistantUnavailable",
    "AssistantBusy",
]

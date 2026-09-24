"""Exceptions for the personal AI assistant.

The route maps `AssistantInputError` to HTTP 400 and `AssistantUnavailable`
to a clean 503 with a friendly body -- never a stack trace, and never a raw
provider error string to the browser.
"""


class AssistantError(Exception):
    """Base class for everything the assistant raises on purpose."""


class AssistantInputError(AssistantError):
    """The caller's message is missing, empty, or too long."""


class AssistantUnavailable(AssistantError):
    """A dependency the assistant needs is not usable right now: no API
    key, the LLM provider errored or rate-limited us, the embedder isn't
    installed, or retrieval failed. Recoverable from the user's point of
    view -- "try again later" -- so it must not 500."""


class AssistantBusy(AssistantUnavailable):
    """Every model in the chain is rate-limited right now. A subclass of
    `AssistantUnavailable`, not a sibling -- an `except AssistantUnavailable`
    written before this class existed still catches it (as the generic
    "offline" 503) if a caller hasn't been updated to special-case it yet.
    The route is expected to catch this *first* and answer HTTP 429 with a
    `Retry-After` header and a "busy, try again shortly" body, distinct from
    the plain 503 `AssistantUnavailable` gets for "the assistant is broken."

    `retry_after` is the number of seconds (best-effort, provider-reported
    or a conservative default) the caller should wait before trying again.
    `None` only if no estimate at all was available, which should be rare
    since callers of this class always pass one."""

    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after

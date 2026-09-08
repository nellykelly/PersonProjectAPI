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

"""The Timed-Squares tool Hera may call -- schema plus a dispatcher.

Unlike the job-tracker tools, this one has no authorization gate: the
leaderboard is public read-only data, visible to every visitor with no
login (see `TimedSquaresScore`'s docstring), and the web route that serves
the same query (`GET /projects/timed-squares/api/leaderboard`) has no rate
limit of its own -- so this tool adds neither an auth check nor a rate
limit that doesn't already exist elsewhere. It is offered to every caller,
signed in or anonymous.

Same contract as `job_tools.py`: `dispatch_timedsquares_tool` never raises
-- a bad argument or an unexpected exception comes back as a short string
for the model to read and relay.
"""
from __future__ import annotations

import json
from typing import Any

from app.services import timed_squares

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 25


# --------------------------------------------------------------------------
# Schema
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


_TOOL_SPECS = [
    _tool(
        "get_leaderboard",
        "Look up the public Timed-Squares leaderboard, ranked by turns "
        "survived (highest first).",
        {
            "limit": {
                "type": "integer",
                "description": f"How many top scores to return; defaults to "
                f"{_DEFAULT_LIMIT}, capped at {_MAX_LIMIT}.",
            }
        },
        [],
    ),
]


def build_timedsquares_tools() -> list[dict]:
    """The one tool schema to hand the model. No authorization gate --
    fresh copy each call so callers can't mutate the module list."""
    return [json.loads(json.dumps(spec)) for spec in _TOOL_SPECS]


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

def _render_row(rank: int, d: dict) -> str:
    name = d.get("player_name") or "ANON"
    return f"{rank}. {name} -- {d['turns_survived']} turns"


def _get_leaderboard(args: dict) -> str:
    limit = args.get("limit")
    if limit in (None, ""):
        limit = _DEFAULT_LIMIT
    else:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            return "The 'limit' value needs to be a whole number."
    if limit <= 0:
        limit = _DEFAULT_LIMIT
    limit = min(limit, _MAX_LIMIT)

    rows = timed_squares.list_leaderboard(limit=limit)
    if not rows:
        return "No Timed-Squares scores on the leaderboard yet."
    lines = "\n".join(_render_row(i, d) for i, d in enumerate(rows, start=1))
    return f"Timed-Squares leaderboard (top {len(rows)}):\n{lines}"


_HANDLERS = {
    "get_leaderboard": _get_leaderboard,
}


def dispatch_timedsquares_tool(name: str, arguments: Any) -> str:
    """Execute one tool call and return a short string for the model,
    always. Never raises: an unknown tool, a bad argument type, or any
    unexpected exception all come back as text."""
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
    except Exception as exc:  # noqa: BLE001 - the model must never see a trace
        return f"That didn't work ({type(exc).__name__})."

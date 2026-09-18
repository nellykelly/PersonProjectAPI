"""The Site Traffic Analytics tool Hera may call -- schema plus a dispatcher.

Same shape as `timedsquares_tools.py`: no authorization gate, since this
wraps the same aggregate, visitor-agnostic numbers the public
`/projects/network-sniffer` dashboard already renders for anyone (never
individual requests or IPs -- see `net_monitor.get_analytics()`'s own
docstring). `dispatch_traffic_tool` never raises -- a bad argument or an
unexpected exception comes back as a short string for the model to read
and relay, exactly like every other public tool domain.
"""
from __future__ import annotations

import json
from typing import Any

from app.services import net_monitor

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
        "get_traffic_summary",
        "Look up this site's own live traffic analytics -- request volume "
        "over time, latency percentiles, error rate, and the busiest "
        "endpoints and outbound calls. Call this whenever someone asks to "
        "see the site's traffic, request volume, latency, or error rate, "
        "or asks for a graph/chart of it -- a chart of the request-volume "
        "trend is rendered automatically in the chat alongside your reply, "
        "so answer as if the visitor can already see it.",
        {},
        [],
    ),
]


def build_traffic_tools() -> list[dict]:
    """The one tool schema to hand the model. No authorization gate --
    fresh copy each call so callers can't mutate the module list."""
    return [json.loads(json.dumps(spec)) for spec in _TOOL_SPECS]


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


def _fmt_latency(label: str, summary: dict) -> str:
    if summary.get("p50") is None:
        return f"{label} latency: no timed requests yet."
    return (
        f"{label} latency (ms): p50 {summary['p50']}, p90 {summary['p90']}, "
        f"p99 {summary['p99']}, max {summary['max']}"
    )


def _get_traffic_summary(_args: dict) -> str:
    stats = net_monitor.get_analytics()
    if not stats["total"]:
        return "No traffic recorded in the current buffer yet."

    lines = [
        f"Traffic buffer: {stats['total']} requests ({stats['inbound']} inbound, "
        f"{stats['outbound']} outbound calls this app made).",
        _fmt_latency("Inbound", stats["inbound_latency"]),
        _fmt_latency("Outbound", stats["outbound_latency"]),
    ]

    if stats["client_error_rate_pct"] is not None:
        lines.append(
            f"Inbound error rate: {stats['client_error_rate_pct']}% 4xx, "
            f"{stats['server_error_rate_pct']}% 5xx."
        )

    if stats["top_endpoints"]:
        top = ", ".join(
            f"{e['endpoint']} ({e['count']})" for e in stats["top_endpoints"][:5]
        )
        lines.append(f"Busiest endpoints: {top}.")

    if stats["top_outbound_hosts"]:
        hosts = ", ".join(
            f"{h['host']} ({h['count']})" for h in stats["top_outbound_hosts"][:5]
        )
        lines.append(f"Busiest outbound hosts: {hosts}.")

    return "\n".join(lines)


_HANDLERS = {
    "get_traffic_summary": _get_traffic_summary,
}


def dispatch_traffic_tool(name: str, arguments: Any) -> str:
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

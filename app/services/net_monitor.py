"""Network Sniffer project: logs the app's OWN traffic only.

Scope, by design (see docs/build-spec.md, Project 3): capturing arbitrary
visitors' network traffic is a wiretapping/privacy problem in most
jurisdictions, regardless of intent, and would violate any host's ToS.
This module never touches a visitor's browsing traffic -- it only records:

  1. Inbound requests this Flask app receives on its own routes
     (via before/after_request hooks registered app-wide).
  2. Outbound calls this app itself makes (yfinance, SEC EDGAR, etc.),
     logged explicitly at the call site by market_data.py / edgar.py.

Kept in-memory (a bounded, thread-safe ring buffer) rather than persisted
to the database -- it's a live dashboard, not an audit trail, and
resetting on restart is an acceptable, simpler trade-off at this scope.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone

from flask import Flask, g, request

_lock = threading.Lock()
_buffer: deque[dict] = deque(maxlen=500)

# Endpoints excluded from the log: static asset requests are noise, not
# meaningful "what is this app doing over the network" signal.
_EXCLUDED_ENDPOINTS = {"static", "sniffer.api_log"}


def configure(buffer_size: int) -> None:
    global _buffer
    with _lock:
        _buffer = deque(_buffer, maxlen=buffer_size)


def _record(entry: dict) -> None:
    with _lock:
        _buffer.append(entry)


def log_inbound(method: str, path: str, status_code: int, duration_ms: float | None) -> None:
    _record(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "direction": "in",
            "method": method,
            "target": path,
            "status": status_code,
            "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
        }
    )


def log_outbound(source: str, method: str, url: str, status_code: int | None, duration_ms: float) -> None:
    _record(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "direction": "out",
            "method": method,
            "target": url,
            "source": source,
            "status": status_code,
            "duration_ms": round(duration_ms, 1),
        }
    )


def get_recent(limit: int = 200) -> list[dict]:
    with _lock:
        items = list(_buffer)[-limit:]
    items.reverse()
    return items


def get_stats() -> dict:
    with _lock:
        items = list(_buffer)

    inbound = [i for i in items if i["direction"] == "in"]
    outbound = [i for i in items if i["direction"] == "out"]
    durations = [i["duration_ms"] for i in items if i.get("duration_ms") is not None]

    hosts: dict[str, int] = {}
    for i in outbound:
        host = i["target"].split("/")[2] if "://" in i["target"] else i["target"]
        hosts[host] = hosts.get(host, 0) + 1

    return {
        "total": len(items),
        "inbound": len(inbound),
        "outbound": len(outbound),
        "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
        "outbound_by_host": hosts,
    }


def register_request_hooks(app: Flask) -> None:
    configure(app.config.get("NET_MONITOR_BUFFER_SIZE", 500))

    @app.before_request
    def _net_monitor_start_timer():
        g._net_monitor_start = time.time()

    @app.after_request
    def _net_monitor_log_inbound(response):
        if request.endpoint not in _EXCLUDED_ENDPOINTS:
            start = getattr(g, "_net_monitor_start", None)
            duration_ms = (time.time() - start) * 1000 if start is not None else None
            log_inbound(request.method, request.path, response.status_code, duration_ms)
        return response

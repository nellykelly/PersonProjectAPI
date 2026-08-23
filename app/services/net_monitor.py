"""Site Traffic Analytics project: logs the app's OWN traffic only, and
aggregates it into a real analytics board rather than a raw live log.

Scope, by design (see docs/build-spec.md, Project 3): capturing arbitrary
visitors' network traffic is a wiretapping/privacy problem in most
jurisdictions, regardless of intent, and would violate any host's ToS.
This module never touches a visitor's browsing traffic -- it only records:

  1. Inbound requests this Flask app receives on its own routes
     (via before/after_request hooks registered app-wide).
  2. Outbound calls this app itself makes (yfinance, SEC EDGAR, etc.),
     logged explicitly at the call site by market_data.py / edgar.py.

Kept in-memory (a bounded, thread-safe ring buffer) rather than persisted
to the database -- this is a live rollup over recent traffic, not an
audit trail, and resetting on restart is an acceptable, simpler trade-off
at this scope. `get_analytics()` computes everything (volume over time,
latency percentiles, error rate, top endpoints/hosts) from that same
buffer with plain Python -- no numpy, no time-series database, the same
"hand-roll the small stats, don't reach for a heavy dependency" choice
already made for the QR Scorer's backtest correlation.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from flask import Flask, g, request

_lock = threading.Lock()
_buffer: deque[dict] = deque(maxlen=500)

# Endpoints excluded from the log: static asset requests and this
# project's own analytics endpoint are noise, not meaningful "what is
# this app doing over the network" signal.
_EXCLUDED_ENDPOINTS = {"static", "sniffer.api_analytics"}

# The analytics board buckets whatever span of time the buffer currently
# holds into this many buckets, so the volume chart adapts to actual
# traffic rather than assuming a fixed window -- a quiet personal site
# and a suddenly-busy one both render sensibly.
VOLUME_BUCKET_COUNT = 12


def configure(buffer_size: int) -> None:
    global _buffer
    with _lock:
        _buffer = deque(_buffer, maxlen=buffer_size)


def _record(entry: dict) -> None:
    with _lock:
        _buffer.append(entry)


def reset_for_tests() -> None:
    """Test-only: clears the buffer. The buffer is module-level state
    shared across the whole test session (there's no per-test app
    instance for it), so a test that needs an exact count -- not just a
    before/after delta -- needs to start from a known-empty buffer.
    Never called from application code."""
    with _lock:
        _buffer.clear()


def log_inbound(
    method: str, path: str, status_code: int, duration_ms: float | None, endpoint: str | None = None
) -> None:
    _record(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "direction": "in",
            "method": method,
            "target": path,
            # The Flask endpoint name (e.g. "trading.position_detail"), not
            # the raw path -- grouping "top endpoints" by raw path would
            # fragment every dynamic route (/positions/7, /positions/8, ...)
            # into its own bucket instead of one meaningful row.
            "endpoint": endpoint,
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


def _host_of(target: str) -> str:
    """Extract a grouping key for the 'outbound calls by host' breakdown.

    Real http(s) URLs (edgar.py) group by their actual hostname. Synthetic
    pseudo-URLs (market_data.py logs yfinance calls as e.g.
    "yfinance://AAPL/info", since yfinance manages its own HTTP client
    internally -- there's no real URL to point at) would otherwise have
    the ticker land in the host slot via a naive `split("/")[2]` -- so
    anything that isn't http(s) groups by its scheme name instead.
    """
    if target.startswith("http://") or target.startswith("https://"):
        parts = target.split("/")
        return parts[2] if len(parts) > 2 else target
    if "://" in target:
        return target.split("://", 1)[0]
    return target


def _percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (the same convention numpy's
    default uses) -- hand-rolled rather than pulling in a dependency for
    one function, the same call made for the QR Scorer's backtest
    correlation. `values` must be non-empty."""
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100)
    lower = int(k)
    upper = min(lower + 1, len(s) - 1)
    if lower == upper:
        return s[lower]
    return s[lower] + (s[upper] - s[lower]) * (k - lower)


def _latency_summary(durations: list[float]) -> dict:
    if not durations:
        return {"p50": None, "p90": None, "p99": None, "max": None}
    return {
        "p50": round(_percentile(durations, 50), 1),
        "p90": round(_percentile(durations, 90), 1),
        "p99": round(_percentile(durations, 99), 1),
        "max": round(max(durations), 1),
    }


def _volume_buckets(items: list[dict], bucket_count: int = VOLUME_BUCKET_COUNT) -> list[dict]:
    """Inbound/outbound counts across `bucket_count` equal-width buckets
    spanning the buffer's own oldest-to-newest timestamps. Returns an
    empty list rather than dividing by a zero-length span when the
    buffer is empty or holds a single instant."""
    if not items:
        return []
    timestamps = [datetime.fromisoformat(i["ts"]) for i in items]
    start, end = min(timestamps), max(timestamps)
    span_seconds = (end - start).total_seconds()
    bucket_seconds = span_seconds / bucket_count if span_seconds > 0 else 1.0

    buckets = [
        {"start": (start + timedelta(seconds=bucket_seconds * n)).isoformat(), "inbound": 0, "outbound": 0}
        for n in range(bucket_count)
    ]
    for entry, ts in zip(items, timestamps):
        offset = (ts - start).total_seconds()
        idx = min(int(offset / bucket_seconds), bucket_count - 1) if span_seconds > 0 else 0
        buckets[idx]["inbound" if entry["direction"] == "in" else "outbound"] += 1
    return buckets


def get_analytics() -> dict:
    """Everything the analytics board renders, computed fresh from the
    current buffer -- volume over time, latency percentiles split by
    direction, inbound error rate, and the top endpoints/outbound hosts
    by call volume."""
    with _lock:
        items = list(_buffer)

    inbound = [i for i in items if i["direction"] == "in"]
    outbound = [i for i in items if i["direction"] == "out"]

    inbound_durations = [i["duration_ms"] for i in inbound if i.get("duration_ms") is not None]
    outbound_durations = [i["duration_ms"] for i in outbound if i.get("duration_ms") is not None]

    client_errors = sum(1 for i in inbound if i["status"] is not None and 400 <= i["status"] < 500)
    server_errors = sum(1 for i in inbound if i["status"] is not None and i["status"] >= 500)

    endpoint_stats: dict[str, dict] = {}
    for i in inbound:
        key = i.get("endpoint") or i["target"]
        bucket = endpoint_stats.setdefault(key, {"count": 0, "durations": []})
        bucket["count"] += 1
        if i.get("duration_ms") is not None:
            bucket["durations"].append(i["duration_ms"])
    top_endpoints = sorted(
        (
            {
                "endpoint": key,
                "count": b["count"],
                "avg_duration_ms": round(sum(b["durations"]) / len(b["durations"]), 1) if b["durations"] else None,
            }
            for key, b in endpoint_stats.items()
        ),
        key=lambda e: e["count"],
        reverse=True,
    )[:8]

    host_counts: dict[str, int] = {}
    for i in outbound:
        host = _host_of(i["target"])
        host_counts[host] = host_counts.get(host, 0) + 1
    top_outbound_hosts = sorted(
        ({"host": h, "count": c} for h, c in host_counts.items()), key=lambda x: x["count"], reverse=True
    )[:8]

    return {
        "total": len(items),
        "inbound": len(inbound),
        "outbound": len(outbound),
        "client_error_count": client_errors,
        "server_error_count": server_errors,
        "client_error_rate_pct": round(client_errors / len(inbound) * 100, 1) if inbound else None,
        "server_error_rate_pct": round(server_errors / len(inbound) * 100, 1) if inbound else None,
        "inbound_latency": _latency_summary(inbound_durations),
        "outbound_latency": _latency_summary(outbound_durations),
        "top_endpoints": top_endpoints,
        "top_outbound_hosts": top_outbound_hosts,
        "volume_buckets": _volume_buckets(items),
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
            log_inbound(request.method, request.path, response.status_code, duration_ms, request.endpoint)
        return response

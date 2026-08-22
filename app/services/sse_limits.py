"""Concurrency caps for the app's long-lived SSE (Server-Sent Events)
connections -- the Trading Simulator's risk feed and watchlist stream,
and the Network Sniffer's live stream. Each of those views holds a
worker thread open indefinitely for as long as a browser tab stays
connected, and gunicorn only has a small, fixed pool of them (see the
Dockerfile's `-w 2 --threads 4` = 8 total request-handling slots).
Without a cap, a handful of concurrent connections from one visitor can
occupy every thread in every worker and hang the *entire site* for
everyone else -- not just degrade one feature. This module is what
actually enforces the cap.

Backed by Redis (the same connection Pipeline World's queue already
uses, see queue.py) rather than an in-process counter, specifically so
the cap holds across gunicorn's multiple worker *processes* in real
deployment -- a plain Python-memory semaphore would only cap one
process's share of the thread pool, letting an attacker still exhaust
the other worker's threads.

Three limits are enforced together for every connection attempt:
  - `max_global`: a hard ceiling across *all* SSE categories combined,
    sized to leave some of the shared thread pool free for ordinary page
    loads no matter which single feed is being hammered.
  - `max_per_category`: a ceiling on one specific feed (risk-feed,
    watchlist, sniffer) so one feature can't consume the whole global
    budget by itself.
  - `max_per_client`: a ceiling per IP address, so one visitor can't
    single-handedly exhaust either of the above with many tabs/scripts.

Each counter is a plain Redis INCR/DECR pair, released in the SSE
generator's `finally` block the same way the watchlist's own
subscribe/unsubscribe already relies on `finally` for cleanup on
disconnect. As a safety net against a worker crashing mid-connection
(skipping that `finally`), each counter gets a TTL the *first* time it's
created (not refreshed on every new connection) -- so a leaked count
self-heals within that window even under continued traffic, rather than
having its expiry pushed out forever by newer connections.
"""
from __future__ import annotations

from app.services import queue as queue_service

# Coarse safety net, not the primary cleanup mechanism (that's the
# `finally`-block release on normal disconnect) -- bounds how long a
# leaked count from a crashed worker can linger before self-healing.
COUNTER_TTL_SECONDS = 600

# 8 total gunicorn request-handling slots (see module docstring): capping
# at 6 left only 2 slots free for every other request on the site while
# any handful of SSE viewers were connected. Lowered to leave real headroom.
GLOBAL_SSE_CAP = 4
CATEGORY_SSE_CAP = 3
PER_CLIENT_SSE_CAP = 2


class TooManyConnections(Exception):
    """Raised when a connection attempt would exceed the global,
    per-category, or per-client SSE concurrency cap. The caller should
    respond with a 429 rather than opening the stream."""


def _incr_with_ttl(conn, key: str) -> int:
    value = conn.incr(key)
    if value == 1:
        conn.expire(key, COUNTER_TTL_SECONDS)
    return value


def acquire_sse_slot(
    category: str,
    client_key: str,
    *,
    max_per_client: int = PER_CLIENT_SSE_CAP,
    max_per_category: int = CATEGORY_SSE_CAP,
    max_global: int = GLOBAL_SSE_CAP,
) -> None:
    """Reserves one concurrent connection slot for `category`, scoped to
    `client_key` (the caller's IP address). Raises TooManyConnections
    immediately -- before any streaming response is built -- if any of
    the three caps is already at its limit; call `release_sse_slot` with
    the same arguments when the connection ends."""
    conn = queue_service.get_redis_connection()
    global_key = "sse:global:total"
    category_key = f"sse:{category}:total"
    client_key_full = f"sse:{category}:client:{client_key}"

    if _incr_with_ttl(conn, global_key) > max_global:
        conn.decr(global_key)
        raise TooManyConnections("The site is at its live-connection limit right now -- try again shortly.")

    if _incr_with_ttl(conn, category_key) > max_per_category:
        conn.decr(category_key)
        conn.decr(global_key)
        raise TooManyConnections(f"Too many concurrent connections to {category} right now -- try again shortly.")

    if _incr_with_ttl(conn, client_key_full) > max_per_client:
        conn.decr(client_key_full)
        conn.decr(category_key)
        conn.decr(global_key)
        raise TooManyConnections(f"You already have {max_per_client} open connections to {category} -- close one first.")


def release_sse_slot(category: str, client_key: str) -> None:
    """Releases a slot reserved by a *successful* `acquire_sse_slot` call
    for the same (category, client_key). Only call this when the matching
    acquire actually returned (didn't raise) -- a raised
    TooManyConnections already rolls back every counter it touched
    before propagating, so calling release afterward would double-
    decrement and drive a counter negative."""
    conn = queue_service.get_redis_connection()
    conn.decr("sse:global:total")
    conn.decr(f"sse:{category}:total")
    conn.decr(f"sse:{category}:client:{client_key}")


def active_connections(category: str) -> int:
    """The current live count of open connections for `category` -- an
    exact, real-time reading of the same counter `acquire_sse_slot`/
    `release_sse_slot` maintain, useful anywhere that wants to display
    "how many live feeds are open right now" (see risk_dashboard.py)
    without inferring it from unrelated data."""
    conn = queue_service.get_redis_connection()
    return int(conn.get(f"sse:{category}:total") or 0)

"""Cache-aside for Pipeline World's live world state (SRE Infra Layer's
caching showcase -- named explicitly since that's the pattern the spec
asks to call out).

Reads try Redis first; on a miss, fall back to the database, then
repopulate the cache. A successful Deploy invalidates the cache so the
next read picks up the newly-live character. Hit/miss counters are kept
in Redis too, for the SRE Infra dashboard.
"""
from __future__ import annotations

import json

from flask import current_app

from app.services.queue import get_redis_connection

WORLD_CACHE_KEY = "pipeline_world:live_characters"
HITS_KEY = "pipeline_world:cache:hits"
MISSES_KEY = "pipeline_world:cache:misses"


def get_live_world() -> list[dict]:
    conn = get_redis_connection()
    cached = conn.get(WORLD_CACHE_KEY)
    if cached is not None:
        conn.incr(HITS_KEY)
        return json.loads(cached)

    conn.incr(MISSES_KEY)

    from app.models import Character

    characters = Character.query.filter_by(status="live").all()
    payload = [c.to_dict() for c in characters]

    ttl = current_app.config.get("WORLD_CACHE_TTL_SECONDS", 30)
    conn.set(WORLD_CACHE_KEY, json.dumps(payload), ex=ttl)
    return payload


def invalidate_world_cache() -> None:
    get_redis_connection().delete(WORLD_CACHE_KEY)


def get_cache_stats() -> dict:
    conn = get_redis_connection()
    hits = int(conn.get(HITS_KEY) or 0)
    misses = int(conn.get(MISSES_KEY) or 0)
    total = hits + misses
    return {
        "hits": hits,
        "misses": misses,
        "hit_rate_pct": round(hits / total * 100, 1) if total else None,
    }

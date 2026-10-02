"""Tests for app.services.assistant.cache -- the standalone repeat-answer
cache. Nothing here wires the cache into the orchestrator (that's a later
task); these tests exercise lookup()/store()/is_cacheable_turn()/
invalidate_all() directly against the app's fakeredis connection."""
from __future__ import annotations

import pytest

from app.extensions import db
from app.models import ContentChunk
from app.services.assistant import cache


def _seed_chunk(source="bio", content="Nelson builds things."):
    db.create_all()
    db.session.add(
        ContentChunk(
            source=source,
            kind="bio",
            title=source,
            chunk_index=0,
            content=content,
            token_estimate=4,
        )
    )
    db.session.commit()


@pytest.fixture()
def ctx(app):
    with app.app_context():
        _seed_chunk()
        yield app


# ---------- lookup / store round trip ----------


def test_miss_then_hit(ctx):
    assert cache.lookup("What does he do?", ctx.config) is None
    payload = {"reply": "He builds things.", "sources": [], "charts": [], "model": "m1"}
    cache.store("What does he do?", payload, ctx.config)
    assert cache.lookup("What does he do?", ctx.config) == payload


def test_normalization_treats_equivalent_questions_as_the_same_key(ctx):
    payload = {"reply": "He builds things.", "sources": [], "charts": [], "model": "m1"}
    cache.store("What does he do?", payload, ctx.config)
    assert cache.lookup("  what does he do ", ctx.config) == payload
    assert cache.lookup("What Does He Do", ctx.config) == payload


def test_different_question_is_a_miss(ctx):
    payload = {"reply": "He builds things.", "sources": [], "charts": [], "model": "m1"}
    cache.store("What does he do?", payload, ctx.config)
    assert cache.lookup("What's his best project?", ctx.config) is None


def test_ttl_is_set_on_store(ctx):
    payload = {"reply": "He builds things.", "sources": [], "charts": [], "model": "m1"}
    cache.store("What does he do?", payload, ctx.config)
    key = cache._cache_key("What does he do?", ctx.config)
    from app.services.queue import get_redis_connection

    ttl = get_redis_connection().ttl(key)
    assert 0 < ttl <= 21600


def test_custom_ttl_is_honoured(ctx):
    ctx.config["ASSISTANT_CACHE_TTL_SECONDS"] = 60
    payload = {"reply": "He builds things.", "sources": [], "charts": [], "model": "m1"}
    cache.store("What does he do?", payload, ctx.config)
    key = cache._cache_key("What does he do?", ctx.config)
    from app.services.queue import get_redis_connection

    ttl = get_redis_connection().ttl(key)
    assert 0 < ttl <= 60


# ---------- version bumps ----------


def test_content_version_bump_is_a_miss(ctx):
    payload = {"reply": "He builds things.", "sources": [], "charts": [], "model": "m1"}
    cache.store("What does he do?", payload, ctx.config)
    assert cache.lookup("What does he do?", ctx.config) == payload

    _seed_chunk(source="new-project", content="A brand new project.")

    assert cache.lookup("What does he do?", ctx.config) is None


def test_prompt_version_change_is_a_miss(ctx, monkeypatch):
    payload = {"reply": "He builds things.", "sources": [], "charts": [], "model": "m1"}
    cache.store("What does he do?", payload, ctx.config)
    assert cache.lookup("What does he do?", ctx.config) == payload

    import app.services.assistant.prompts as prompts

    monkeypatch.setattr(prompts, "SYSTEM_PROMPT", prompts.SYSTEM_PROMPT + " extra")

    assert cache.lookup("What does he do?", ctx.config) is None


def test_config_fingerprint_change_is_a_miss(ctx):
    payload = {"reply": "He builds things.", "sources": [], "charts": [], "model": "m1"}
    cache.store("What does he do?", payload, ctx.config)
    assert cache.lookup("What does he do?", ctx.config) == payload

    ctx.config["GROQ_MODEL"] = "some-other-model"

    assert cache.lookup("What does he do?", ctx.config) is None


@pytest.mark.parametrize(
    "key,changed",
    [
        ("ASSISTANT_RETRIEVAL_MIN_SCORE", 0.9),
        ("ASSISTANT_RETRIEVAL_SCORE_MARGIN", 0.9),
        ("ASSISTANT_MAX_CONTEXT_CHARS", 1),
        ("ASSISTANT_SKIP_RETRIEVAL_FOR_SMALL_TALK", False),
        ("ASSISTANT_MAX_OUTPUT_TOKENS", 1),
    ],
)
def test_retrieval_and_output_config_changes_are_also_misses(ctx, key, changed):
    """Red-cell M4: the fingerprint originally covered only the model and
    ASSISTANT_RETRIEVAL_TOP_K, so tuning any of these left a stale cached
    answer (shaped by the old settings) alive until the TTL expired."""
    payload = {"reply": "He builds things.", "sources": [], "charts": [], "model": "m1"}
    cache.store("What does he do?", payload, ctx.config)
    assert cache.lookup("What does he do?", ctx.config) == payload

    ctx.config[key] = changed

    assert cache.lookup("What does he do?", ctx.config) is None


# ---------- disabled / fail-open ----------


def test_disabled_cache_always_misses_and_never_stores(ctx):
    ctx.config["ASSISTANT_CACHE_ENABLED"] = False
    payload = {"reply": "He builds things.", "sources": [], "charts": [], "model": "m1"}
    cache.store("What does he do?", payload, ctx.config)
    assert cache.lookup("What does he do?", ctx.config) is None

    ctx.config["ASSISTANT_CACHE_ENABLED"] = True
    assert cache.lookup("What does he do?", ctx.config) is None


def test_redis_error_on_lookup_fails_open(ctx, monkeypatch):
    class _Boom:
        def get(self, key):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(cache, "get_redis_connection", lambda: _Boom())
    assert cache.lookup("What does he do?", ctx.config) is None


def test_redis_error_on_store_fails_open(ctx, monkeypatch):
    class _Boom:
        def set(self, key, value, ex=None):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(cache, "get_redis_connection", lambda: _Boom())
    # Must not raise.
    cache.store("What does he do?", {"reply": "x"}, ctx.config)


def test_db_error_during_key_computation_fails_open(ctx, monkeypatch):
    def _boom():
        raise RuntimeError("db is down")

    monkeypatch.setattr(cache, "_content_version", _boom)
    assert cache.lookup("What does he do?", ctx.config) is None
    # store() must also swallow it rather than raising.
    cache.store("What does he do?", {"reply": "x"}, ctx.config)


# ---------- is_cacheable_turn ----------


def _turn(**overrides):
    base = dict(
        history=[],
        is_owner=False,
        job_tools_authorized=False,
        tool_trace=[],
        reply="A real answer.",
        error=False,
    )
    base.update(overrides)
    return base


def test_is_cacheable_turn_happy_path():
    assert cache.is_cacheable_turn(**_turn()) is True


def test_is_cacheable_turn_rejects_non_first_turn():
    assert cache.is_cacheable_turn(**_turn(history=[{"role": "user", "content": "hi"}])) is False


def test_is_cacheable_turn_rejects_owner():
    assert cache.is_cacheable_turn(**_turn(is_owner=True)) is False


def test_is_cacheable_turn_rejects_job_tools_authorized():
    assert cache.is_cacheable_turn(**_turn(job_tools_authorized=True)) is False


def test_is_cacheable_turn_rejects_tool_calls():
    assert cache.is_cacheable_turn(**_turn(tool_trace=["get_traffic_summary"])) is False


def test_is_cacheable_turn_rejects_error():
    assert cache.is_cacheable_turn(**_turn(error=True)) is False


def test_is_cacheable_turn_rejects_empty_reply():
    assert cache.is_cacheable_turn(**_turn(reply="")) is False
    assert cache.is_cacheable_turn(**_turn(reply="   ")) is False
    assert cache.is_cacheable_turn(**_turn(reply=None)) is False


# ---------- invalidate_all ----------


def test_invalidate_all_clears_only_cache_keys(ctx):
    cache.store("What does he do?", {"reply": "a"}, ctx.config)
    cache.store("What's his best project?", {"reply": "b"}, ctx.config)

    from app.services.queue import get_redis_connection

    conn = get_redis_connection()
    conn.set("some:unrelated:key", "keep-me")

    deleted = cache.invalidate_all()
    assert deleted == 2

    assert cache.lookup("What does he do?", ctx.config) is None
    assert cache.lookup("What's his best project?", ctx.config) is None
    assert conn.get("some:unrelated:key") == b"keep-me"


def test_invalidate_all_returns_zero_when_empty(ctx):
    assert cache.invalidate_all() == 0


def test_invalidate_all_fails_open(ctx, monkeypatch):
    class _Boom:
        def scan_iter(self, match=None):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(cache, "get_redis_connection", lambda: _Boom())
    assert cache.invalidate_all() == 0


# ---------- semantic cache layer ----------
#
# TestingConfig forces ASSISTANT_EMBEDDER="hash" (see assistant/README.md),
# so these run against the deterministic, dependency-free HashEmbedder, not
# fastembed -- no model download, no network call, same as every other
# offline test here. HashEmbedder is a crude bag-of-words/trigram hash, not
# truly semantic, so these tests pick a threshold that isolates the
# *mechanism* (compare, gate on threshold, respect the fingerprint) rather
# than asserting any particular real-world paraphrase similarity -- that
# empirical tuning is the live fastembed sweep this task's report covers.


def _enable_semantic(config, threshold=0.5):
    config["ASSISTANT_SEMANTIC_CACHE_ENABLED"] = True
    config["ASSISTANT_SEMANTIC_CACHE_THRESHOLD"] = threshold


def test_semantic_lookup_is_off_by_default(ctx):
    cache.store("Tell me about the trading simulator", {"reply": "a"}, ctx.config)
    assert cache.semantic_lookup("Walk me through the trading simulator project", ctx.config) is None


def test_semantic_lookup_disabled_store_attaches_no_vector(ctx):
    import json

    from app.services.queue import get_redis_connection

    cache.store("Tell me about the trading simulator", {"reply": "a"}, ctx.config)
    key = cache._cache_key("Tell me about the trading simulator", ctx.config)

    raw = get_redis_connection().get(key)
    assert "_semantic_vector" not in json.loads(raw)


def test_semantic_lookup_hits_a_close_paraphrase_above_threshold(ctx):
    _enable_semantic(ctx.config, threshold=0.5)
    payload = {"reply": "Black-Scholes, yfinance, OCC option symbols.", "sources": []}
    cache.store("Tell me about the trading simulator", payload, ctx.config)

    hit = cache.semantic_lookup("Walk me through the trading simulator project", ctx.config)

    assert hit == payload


def test_semantic_lookup_misses_a_dissimilar_question_below_threshold(ctx):
    _enable_semantic(ctx.config, threshold=0.5)
    payload = {"reply": "Black-Scholes, yfinance, OCC option symbols.", "sources": []}
    cache.store("Tell me about the trading simulator", payload, ctx.config)

    assert cache.semantic_lookup("What salary should he be offered", ctx.config) is None


def test_semantic_lookup_never_matches_an_exact_duplicate_twice(ctx):
    """An exact-match hit is handled by lookup(); semantic_lookup() on its
    own still finds the entry (it doesn't know lookup() already ran), which
    is correct -- the orchestrator is what skips calling this after an
    exact hit, exercised in test_assistant.py's orchestrator tests."""
    _enable_semantic(ctx.config, threshold=0.5)
    payload = {"reply": "a"}
    cache.store("Tell me about the trading simulator", payload, ctx.config)

    assert cache.semantic_lookup("Tell me about the trading simulator", ctx.config) == payload


def test_semantic_lookup_ignores_entries_from_a_stale_fingerprint(ctx):
    _enable_semantic(ctx.config, threshold=0.5)
    payload = {"reply": "Black-Scholes, yfinance, OCC option symbols.", "sources": []}
    cache.store("Tell me about the trading simulator", payload, ctx.config)

    ctx.config["GROQ_MODEL"] = "some-other-model"

    assert cache.semantic_lookup("Walk me through the trading simulator project", ctx.config) is None


def test_semantic_lookup_returns_the_closest_of_several_candidates(ctx):
    _enable_semantic(ctx.config, threshold=0.1)
    cache.store("What salary should he be offered", {"reply": "far"}, ctx.config)
    cache.store("Tell me about the trading simulator", {"reply": "close"}, ctx.config)

    hit = cache.semantic_lookup("Walk me through the trading simulator project", ctx.config)

    assert hit == {"reply": "close"}


def test_semantic_lookup_fails_open_on_redis_error(ctx, monkeypatch):
    _enable_semantic(ctx.config, threshold=0.5)

    class _Boom:
        def scan_iter(self, match=None):
            raise ConnectionError("redis is down")

    monkeypatch.setattr(cache, "get_redis_connection", lambda: _Boom())
    assert cache.semantic_lookup("Tell me about the trading simulator", ctx.config) is None


def test_cosine_similarity_handles_degenerate_vectors():
    assert cache._cosine_similarity([], [1.0]) == 0.0
    assert cache._cosine_similarity([1.0, 0.0], [1.0]) == 0.0
    assert cache._cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cache._cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

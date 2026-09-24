"""app/services/assistant/backends.py -- rate-limit resilience.

Covers: GroqBackend mapping groq.RateLimitError to AssistantBusy (with
retry_after parsed from response headers, and WITHOUT going through the
plain-retry path -- a 429 must not be retried, it would double-spend and
double the wait); that the plain-retry path (no reasoning params) fires
ONLY for a groq.BadRequestError (the "this model/endpoint doesn't accept
reasoning_format/reasoning_effort" case it exists for) and NOT for a
timeout/connection/5xx-style failure, which fails straight to
AssistantUnavailable with exactly one call -- a timing-out model retried
blind used to cost 2x its timeout, x however many models are in the
fallback chain, of a request thread's time; the thread-safe cooldown
registry; FallbackBackend's model chain (skip cooldown models, fall back on
busy or on any other failure, report the answering model and fell_back on
LLMReply); and build_backend()'s groq-path wiring of
ASSISTANT_GROQ_TIMEOUT_SECONDS / ASSISTANT_GROQ_MAX_RETRIES /
ASSISTANT_FALLBACK_MODELS.

Never touches the network: every test monkeypatches `groq.Groq`,
`groq.RateLimitError` and `groq.BadRequestError` with stubs (same pattern as
test_assistant_guard.py's `_build_client` stubbing) -- GroqBackend/
FallbackBackend import all three names fresh from the `groq` module at call
time, so patching the module attributes is enough; no fixture ever
constructs a real Groq client.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.assistant import backends
from app.services.assistant.backends import AssistantBusy, FallbackBackend, GroqBackend, LLMReply
from app.services.assistant.errors import AssistantUnavailable


# ---------------------------------------------------------------------------
# Stub groq.Groq / groq.RateLimitError
# ---------------------------------------------------------------------------


class _StubRateLimitError(Exception):
    """Stands in for groq.RateLimitError. Only the shape backends.py reads
    matters: `.response.headers` (a plain dict is fine -- our header
    lookups are always by exact lowercase key, same as httpx.Headers.get
    would resolve them)."""

    def __init__(self, message="rate limited", headers=None):
        super().__init__(message)
        self.response = SimpleNamespace(headers=headers if headers is not None else {})


class _StubBadRequestError(Exception):
    """Stands in for groq.BadRequestError (HTTP 400) -- the one failure
    that legitimately means "retry without the reasoning params", e.g. a
    model/endpoint that doesn't accept reasoning_format/reasoning_effort."""


class _StubMessage:
    def __init__(self, content):
        self.content = content
        self.tool_calls = None


class _StubChoice:
    def __init__(self, content):
        self.message = _StubMessage(content)
        self.finish_reason = "stop"


class _StubUsage:
    def __init__(self):
        self.prompt_tokens = 10
        self.completion_tokens = 5


class _StubChatResponse:
    def __init__(self, content):
        self.choices = [_StubChoice(content)]
        self.usage = _StubUsage()


class _StubCompletions:
    """`script` maps model -> a list of scripted actions consumed in order:
    an Exception instance to raise, or a string to return as reply text.
    `calls_log` (shared across every stub client, since FallbackBackend
    builds one Groq client per model) records every create() call's kwargs
    for assertions like "the busy model was never called"."""

    def __init__(self, script, calls_log):
        self._script = script
        self._calls_log = calls_log

    def create(self, **kwargs):
        self._calls_log.append(kwargs)
        model = kwargs["model"]
        queue = self._script.get(model)
        if not queue:
            raise AssertionError(f"no scripted action left for model {model!r}")
        action = queue.pop(0)
        if isinstance(action, Exception):
            raise action
        return _StubChatResponse(action)


class _StubClient:
    def __init__(self, script, calls_log, **init_kwargs):
        self.completions = _StubCompletions(script, calls_log)
        self.chat = SimpleNamespace(completions=self.completions)
        self.init_kwargs = init_kwargs


def _patch_groq(monkeypatch):
    script: dict[str, list] = {}
    calls_log: list[dict] = []
    client_kwargs_log: list[dict] = []

    def factory(**kwargs):
        client_kwargs_log.append(kwargs)
        return _StubClient(script, calls_log, **kwargs)

    monkeypatch.setattr("groq.Groq", factory)
    monkeypatch.setattr("groq.RateLimitError", _StubRateLimitError)
    monkeypatch.setattr("groq.BadRequestError", _StubBadRequestError)
    return SimpleNamespace(script=script, calls=calls_log, client_kwargs=client_kwargs_log)


@pytest.fixture(autouse=True)
def _isolated_backend_state(monkeypatch):
    """Every test gets its own cooldown registry and build_backend() cache
    -- both are process-global in production on purpose, but that would
    let tests bleed into each other here."""
    monkeypatch.setattr(backends, "_COOLDOWNS", backends._CooldownRegistry())
    monkeypatch.setattr(backends, "_CACHE", {})


def _msg(text="hi"):
    return [{"role": "user", "content": text}]


# ---------------------------------------------------------------------------
# GroqBackend: RateLimitError -> AssistantBusy, not retried
# ---------------------------------------------------------------------------


def test_rate_limit_maps_to_assistant_busy_with_parsed_retry_after(monkeypatch):
    stub = _patch_groq(monkeypatch)
    stub.script["m"] = [_StubRateLimitError(headers={"retry-after": "12.5"})]
    backend = GroqBackend("key", "m")

    with pytest.raises(AssistantBusy) as excinfo:
        backend.generate(_msg(), max_tokens=10)

    assert excinfo.value.retry_after == pytest.approx(12.5)


def test_rate_limit_error_is_not_retried_without_reasoning_params(monkeypatch):
    """The plain-chat-completion fallback retry (for "unknown param" style
    errors) must never fire for a 429 -- it would spend a second request
    against an already-exhausted quota."""
    stub = _patch_groq(monkeypatch)
    stub.script["m"] = [_StubRateLimitError(headers={"retry-after": "2"})]
    backend = GroqBackend("key", "m")

    with pytest.raises(AssistantBusy):
        backend.generate(_msg(), max_tokens=10)

    assert len(stub.calls) == 1


def test_rate_limit_registers_a_cooldown_for_its_model(monkeypatch):
    stub = _patch_groq(monkeypatch)
    stub.script["m"] = [_StubRateLimitError(headers={"retry-after": "9"})]
    backend = GroqBackend("key", "m")

    with pytest.raises(AssistantBusy):
        backend.generate(_msg(), max_tokens=10)

    assert backends._COOLDOWNS.remaining("m") == pytest.approx(9, abs=0.5)


def test_rate_limit_with_no_response_headers_falls_back_to_default(monkeypatch):
    stub = _patch_groq(monkeypatch)

    class _BareRateLimitError(Exception):
        pass

    monkeypatch.setattr("groq.RateLimitError", _BareRateLimitError)
    stub.script["m"] = [_BareRateLimitError("no response attribute at all")]
    backend = GroqBackend("key", "m")

    with pytest.raises(AssistantBusy) as excinfo:
        backend.generate(_msg(), max_tokens=10)

    assert excinfo.value.retry_after == backends._DEFAULT_RETRY_AFTER_SECONDS


def test_bad_request_is_retried_once_without_reasoning_params(monkeypatch):
    """The one case the plain-retry path exists for: a 400 meaning the
    model/endpoint rejected reasoning_format/reasoning_effort."""
    stub = _patch_groq(monkeypatch)
    stub.script["m"] = [_StubBadRequestError("unknown parameter reasoning_effort"), "recovered"]
    backend = GroqBackend("key", "m")

    reply = backend.generate(_msg(), max_tokens=10)

    assert reply.text == "recovered"
    assert len(stub.calls) == 2


def test_timeout_is_not_retried_and_fails_straight_to_unavailable(monkeypatch):
    """A timeout says nothing about reasoning params -- retrying blind just
    doubles the wait for a call that will fail the same way again. Must be
    exactly one create() call, not two."""
    stub = _patch_groq(monkeypatch)
    stub.script["m"] = [TimeoutError("timed out")]
    backend = GroqBackend("key", "m")

    with pytest.raises(AssistantUnavailable):
        backend.generate(_msg(), max_tokens=10)

    assert len(stub.calls) == 1


def test_connection_error_is_not_retried_and_fails_straight_to_unavailable(monkeypatch):
    stub = _patch_groq(monkeypatch)
    stub.script["m"] = [ConnectionError("connection reset")]
    backend = GroqBackend("key", "m")

    with pytest.raises(AssistantUnavailable):
        backend.generate(_msg(), max_tokens=10)

    assert len(stub.calls) == 1


# ---------------------------------------------------------------------------
# GroqBackend construction: new kwargs are additive only
# ---------------------------------------------------------------------------


def test_timeout_and_max_retries_forwarded_when_given(monkeypatch):
    stub = _patch_groq(monkeypatch)
    GroqBackend("key", "m", timeout=15, max_retries=0)
    assert stub.client_kwargs[-1] == {"api_key": "key", "timeout": 15, "max_retries": 0}


def test_job_discovery_and_family_style_construction_is_unchanged(monkeypatch):
    """job_discovery.py's `_scoring_backend_cache` and family/chat.py's
    `build_family_backend` both call `GroqBackend(api_key, model[,
    reasoning_effort=...])` with no timeout/max_retries. That must keep
    constructing `Groq(api_key=...)` with nothing extra, so those callers
    keep the SDK's own defaults (60s read timeout, 2 retries) exactly as
    before this change."""
    stub = _patch_groq(monkeypatch)
    GroqBackend("family-key", "openai/gpt-oss-120b")
    GroqBackend("scoring-key", "openai/gpt-oss-120b", reasoning_effort="low")
    assert stub.client_kwargs == [
        {"api_key": "family-key"},
        {"api_key": "scoring-key"},
    ]


# ---------------------------------------------------------------------------
# Groq's rate-limit duration header format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("262ms", 0.262),
        ("7.66s", 7.66),
        ("1m26.4s", 86.4),
    ],
)
def test_parse_groq_duration_formats(raw, expected):
    assert backends._parse_groq_duration(raw) == pytest.approx(expected)


def test_parse_groq_duration_rejects_garbage():
    assert backends._parse_groq_duration("") is None
    assert backends._parse_groq_duration("not-a-duration") is None


def test_retry_after_prefers_the_standard_header():
    headers = {"retry-after": "3", "x-ratelimit-reset-tokens": "1m26.4s"}
    assert backends._retry_after_from_headers(headers) == pytest.approx(3.0)


def test_retry_after_uses_the_longer_of_the_two_groq_reset_headers():
    headers = {
        "x-ratelimit-reset-tokens": "7.66s",
        "x-ratelimit-reset-requests": "262ms",
    }
    assert backends._retry_after_from_headers(headers) == pytest.approx(7.66)


def test_retry_after_defaults_when_nothing_parses():
    assert backends._retry_after_from_headers({}) == backends._DEFAULT_RETRY_AFTER_SECONDS
    assert backends._retry_after_from_headers(None) == backends._DEFAULT_RETRY_AFTER_SECONDS


# ---------------------------------------------------------------------------
# Cooldown registry
# ---------------------------------------------------------------------------


def test_cooldown_registry_reports_busy_until_it_elapses():
    registry = backends._CooldownRegistry()
    assert registry.is_busy("m") is False
    registry.set_busy("m", 30)
    assert registry.is_busy("m") is True
    assert registry.remaining("m") == pytest.approx(30, abs=0.5)


def test_cooldown_registry_never_shortens_an_existing_deadline():
    registry = backends._CooldownRegistry()
    registry.set_busy("m", 30)
    registry.set_busy("m", 5)  # a later, more optimistic estimate
    assert registry.remaining("m") == pytest.approx(30, abs=0.5)


def test_cooldown_registry_tracks_the_reason_a_model_is_cooling_down():
    registry = backends._CooldownRegistry()
    assert registry.reason("m") is None  # never set
    registry.set_busy("m", 30, reason="failure")
    assert registry.reason("m") == "failure"


def test_cooldown_registry_reason_follows_whichever_call_actually_extends_the_deadline():
    """A reason attached to a shorter, non-extending update must not
    overwrite the reason the winning (longer) deadline was set for."""
    registry = backends._CooldownRegistry()
    registry.set_busy("m", 30, reason="rate_limit")
    registry.set_busy("m", 5, reason="failure")  # shorter -- ignored entirely
    assert registry.reason("m") == "rate_limit"
    assert registry.remaining("m") == pytest.approx(30, abs=0.5)


# ---------------------------------------------------------------------------
# FallbackBackend
# ---------------------------------------------------------------------------


def test_primary_rate_limited_falls_back_and_reports_the_answering_model(monkeypatch):
    stub = _patch_groq(monkeypatch)
    stub.script["qwen/qwen3.8-27b"] = [_StubRateLimitError(headers={"retry-after": "5"})]
    stub.script["openai/gpt-oss-20b"] = ["fallback answer"]
    backend = FallbackBackend("key", ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"])

    reply = backend.generate(_msg(), max_tokens=100)

    assert isinstance(reply, LLMReply)
    assert reply.text == "fallback answer"
    assert reply.model == "openai/gpt-oss-20b"
    assert reply.fell_back is True


def test_primary_success_is_not_marked_as_fell_back(monkeypatch):
    stub = _patch_groq(monkeypatch)
    stub.script["m1"] = ["first answer"]
    backend = FallbackBackend("key", ["m1", "m2"])

    reply = backend.generate(_msg(), max_tokens=100)

    assert reply.model == "m1"
    assert reply.fell_back is False


def test_cooldown_skips_a_busy_model_without_calling_it(monkeypatch):
    stub = _patch_groq(monkeypatch)
    backends._COOLDOWNS.set_busy("model-a", 30)
    stub.script["model-b"] = ["answer"]
    backend = FallbackBackend("key", ["model-a", "model-b"])

    reply = backend.generate(_msg(), max_tokens=50)

    assert reply.model == "model-b"
    assert reply.fell_back is True
    assert all(call["model"] != "model-a" for call in stub.calls)


def test_all_models_busy_raises_assistant_busy_with_smallest_wait(monkeypatch):
    stub = _patch_groq(monkeypatch)
    stub.script["model-a"] = [_StubRateLimitError(headers={"retry-after": "30"})]
    stub.script["model-b"] = [_StubRateLimitError(headers={"retry-after": "5"})]
    backend = FallbackBackend("key", ["model-a", "model-b"])

    with pytest.raises(AssistantBusy) as excinfo:
        backend.generate(_msg(), max_tokens=50)

    assert excinfo.value.retry_after == pytest.approx(5.0)


def test_all_models_timing_out_is_an_outage_not_busy(monkeypatch):
    """Red-cell M3: every model failing for a NON-rate-limit reason (a
    Groq outage, every model timing out) must surface as the plain offline
    treatment, not "busy, try again shortly" -- those mean different
    things to a visitor and get counted differently in the stats."""
    stub = _patch_groq(monkeypatch)
    stub.script["model-a"] = [TimeoutError("boom")]
    stub.script["model-b"] = [TimeoutError("boom too")]
    backend = FallbackBackend("key", ["model-a", "model-b"])

    with pytest.raises(AssistantUnavailable) as excinfo:
        backend.generate(_msg(), max_tokens=50)

    assert not isinstance(excinfo.value, AssistantBusy)


def test_a_mix_of_rate_limit_and_timeout_is_still_reported_as_busy(monkeypatch):
    """One real 429 among the failures is enough: the visitor should still
    see "busy" (there IS a quota problem), even though a different model
    in the chain failed for an unrelated reason."""
    stub = _patch_groq(monkeypatch)
    stub.script["model-a"] = [_StubRateLimitError(headers={"retry-after": "8"})]
    stub.script["model-b"] = [TimeoutError("boom")]
    backend = FallbackBackend("key", ["model-a", "model-b"])

    with pytest.raises(AssistantBusy) as excinfo:
        backend.generate(_msg(), max_tokens=50)

    assert excinfo.value.retry_after == pytest.approx(8.0)


def test_skipping_every_model_for_a_prior_failure_cooldown_is_still_an_outage(monkeypatch):
    """Both models are already cooling down from an earlier plain failure
    (not a 429) -- neither is attempted this call at all. That must still
    report as an outage, not busy: a failure cooldown says nothing about
    quota."""
    _patch_groq(monkeypatch)
    backends._COOLDOWNS.set_busy("model-a", 30, reason="failure")
    backends._COOLDOWNS.set_busy("model-b", 30, reason="failure")
    backend = FallbackBackend("key", ["model-a", "model-b"])

    with pytest.raises(AssistantUnavailable) as excinfo:
        backend.generate(_msg(), max_tokens=50)

    assert not isinstance(excinfo.value, AssistantBusy)


def test_skipping_a_model_for_a_prior_rate_limit_cooldown_still_counts_as_busy(monkeypatch):
    """A model already cooling down from an earlier 429 (this call never
    touches it) is still a live quota signal -- skipping it must not let
    the outcome quietly downgrade to "outage" if every other model also
    fails or is skipped."""
    _patch_groq(monkeypatch)
    backends._COOLDOWNS.set_busy("model-a", 45, reason="rate_limit")
    backend = FallbackBackend("key", ["model-a"])

    with pytest.raises(AssistantBusy):
        backend.generate(_msg(), max_tokens=50)


def test_non_rate_limit_failure_falls_back_with_a_short_cooldown(monkeypatch):
    stub = _patch_groq(monkeypatch)
    stub.script["model-a"] = [TimeoutError("boom")]
    stub.script["model-b"] = ["ok"]
    backend = FallbackBackend("key", ["model-a", "model-b"])

    reply = backend.generate(_msg(), max_tokens=10)

    assert reply.model == "model-b"
    assert reply.fell_back is True
    # a timeout must not be retried -- exactly one call for model-a
    assert sum(1 for c in stub.calls if c["model"] == "model-a") == 1
    assert backends._COOLDOWNS.remaining("model-a") == pytest.approx(
        backends._SHORT_COOLDOWN_SECONDS, abs=0.5
    )


def test_duplicate_models_in_the_chain_are_deduplicated(monkeypatch):
    _patch_groq(monkeypatch)
    backend = FallbackBackend("key", ["m1", "m2", "m1", "m2"])
    assert backend._chain == ["m1", "m2"]


def test_gpt_oss_models_get_low_reasoning_effort(monkeypatch):
    _patch_groq(monkeypatch)
    backend = FallbackBackend("key", ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"])
    assert backend._backends["qwen/qwen3.8-27b"]._reasoning_effort is None
    assert backend._backends["openai/gpt-oss-20b"]._reasoning_effort == "low"


def test_empty_chain_raises_assistant_unavailable(monkeypatch):
    _patch_groq(monkeypatch)
    with pytest.raises(AssistantUnavailable):
        FallbackBackend("key", [])


# ---------------------------------------------------------------------------
# build_backend(): config wiring on the groq path
# ---------------------------------------------------------------------------


def _config(**overrides):
    base = {
        "ASSISTANT_LLM_BACKEND": "groq",
        "GROQ_API_KEY": "test-key",
        "GROQ_MODEL": "qwen/qwen3.8-27b",
        "GROQ_TOOL_MODEL": "qwen/qwen3.8-27b",
    }
    base.update(overrides)
    return base


def test_build_backend_for_tools_chains_the_configured_fallback_models(monkeypatch):
    stub = _patch_groq(monkeypatch)
    config = _config(
        ASSISTANT_GROQ_TIMEOUT_SECONDS=15,
        ASSISTANT_GROQ_MAX_RETRIES=0,
        ASSISTANT_FALLBACK_MODELS="openai/gpt-oss-20b,openai/gpt-oss-120b,openai/gpt-oss-20b",
    )

    backend = backends.build_backend(config, for_tools=True)

    assert isinstance(backend, FallbackBackend)
    assert backend._chain == [
        "qwen/qwen3.8-27b",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
    ]
    assert all(
        kw.get("timeout") == 15 and kw.get("max_retries") == 0 for kw in stub.client_kwargs
    )


def test_build_backend_uses_default_fallback_models_when_unset(monkeypatch):
    _patch_groq(monkeypatch)
    config = _config(GROQ_API_KEY="k-default-fallback")

    backend = backends.build_backend(config, for_tools=True)

    assert backend._chain == [
        "qwen/qwen3.8-27b",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
    ]


def test_build_backend_default_timeout_and_max_retries(monkeypatch):
    stub = _patch_groq(monkeypatch)
    config = _config(GROQ_API_KEY="k-default-timeout")

    backends.build_backend(config, for_tools=True)

    assert all(
        kw.get("timeout") == 15 and kw.get("max_retries") == 0 for kw in stub.client_kwargs
    )


def test_build_backend_for_tools_false_uses_primary_model_only(monkeypatch):
    _patch_groq(monkeypatch)
    config = _config(GROQ_API_KEY="k-notools", GROQ_TOOL_MODEL="a-tool-only-model")

    backend = backends.build_backend(config, for_tools=False)

    assert isinstance(backend, FallbackBackend)
    assert backend._chain == ["qwen/qwen3.8-27b"]


def test_build_backend_caches_by_key_chain_and_limits(monkeypatch):
    _patch_groq(monkeypatch)
    config = _config(GROQ_API_KEY="k-cache")

    first = backends.build_backend(config, for_tools=True)
    second = backends.build_backend(config, for_tools=True)

    assert first is second


def test_backend_available_true_makes_no_network_call(monkeypatch):
    stub = _patch_groq(monkeypatch)
    config = _config(GROQ_API_KEY="k-avail")

    assert backends.backend_available(config) is True
    assert stub.calls == []


def test_backend_available_false_without_api_key():
    config = _config(GROQ_API_KEY="")
    assert backends.backend_available(config) is False


# ---------------------------------------------------------------------------
# LLMReply
# ---------------------------------------------------------------------------


def test_llm_reply_fell_back_defaults_to_false():
    reply = LLMReply(text="x", model="m")
    assert reply.fell_back is False

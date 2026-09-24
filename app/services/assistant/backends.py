"""LLM generation backends behind one interface.

`GroqBackend` calls Groq's OpenAI-shaped chat API for one model. `FakeBackend`
is deterministic and offline -- the test suite and any run with
`ASSISTANT_LLM_BACKEND=fake` use it. `ScriptedBackend` is a test-only
backend that replays a queue of pre-programmed turns, used to drive the
tool-calling loop deterministically. `FallbackBackend` wraps an ordered
chain of `GroqBackend`s (the primary chat/tool model plus
`ASSISTANT_FALLBACK_MODELS`) so a rate limit on one model tries the next
one instead of failing the turn; it's what `build_backend()` returns for
`ASSISTANT_LLM_BACKEND=groq`. A future Phase 4 `OllamaBackend` drops in
here with no change above this module.

The one method every backend exposes is
`generate(messages, *, max_tokens, tools=None, tool_choice=None) -> LLMReply`.
`tools` is passed only on the owner's authorized job-tracker path; on every
other call it is `None` and the request is identical to a plain chat
completion.

Any provider failure -- missing key, network, rate limit, bad response --
becomes `AssistantUnavailable`, so the route can return a clean 503 and
never leaks a raw provider error to the browser. A rate limit specifically
becomes `AssistantBusy` (a subclass of `AssistantUnavailable`, so existing
`except AssistantUnavailable` callers are unaffected) carrying a
best-effort `retry_after` in seconds, so the route can instead return a
429 with `Retry-After` and a distinct "busy" message.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, replace

from .errors import AssistantBusy, AssistantUnavailable

# Reasoning models (qwen3, deepseek-r1, ...) prepend a <think>...</think>
# block to their answer. Groq's `reasoning_format="hidden"` strips it
# server-side; this is the client-side backstop, and it also cleans up the
# messier failure the wild has shown -- the opening tag missing, a stray
# </think>, and the answer duplicated on either side of it.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_STRAY_THINK_RE = re.compile(r"</?think>", re.IGNORECASE)


def _strip_reasoning(text: str) -> str:
    text = _THINK_BLOCK_RE.sub("", text)
    low = text.lower()
    if "</think>" in low:
        # unclosed/leaked reasoning: keep only what follows the last </think>
        text = text[low.rindex("</think>") + len("</think>") :]
    text = _STRAY_THINK_RE.sub("", text)
    return text.strip()


@dataclass(frozen=True)
class LLMReply:
    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # Populated only when the model asked to call one or more tools. Each
    # entry is a plain dict {"id", "name", "arguments"} -- `arguments` is
    # the raw JSON string as the provider returned it, so the orchestrator
    # can echo it back verbatim in the assistant turn and json.loads() it
    # for dispatch. Never a raw SDK object, so callers and tests stay
    # provider-agnostic.
    tool_calls: tuple[dict, ...] | None = None
    finish_reason: str | None = None
    # True when `model` was not the first model FallbackBackend tried for
    # this turn -- the primary was rate-limited (or otherwise failing) and
    # a later model in the chain answered instead. Always False for a bare
    # GroqBackend/FakeBackend/ScriptedBackend reply. Frozen dataclass, so
    # FallbackBackend sets this with `dataclasses.replace()` rather than
    # mutating the reply the inner backend returned.
    fell_back: bool = False


# ---------------------------------------------------------------------------
# Rate-limit handling: cooldowns and Groq's rate-limit header formats.
# ---------------------------------------------------------------------------

# Used when a 429 carries no header we can parse at all -- long enough that
# a naive retry-loop caller doesn't hammer the model, short enough that a
# real visitor waiting on a "busy" message isn't stuck long.
_DEFAULT_RETRY_AFTER_SECONDS = 20.0

# Cooldown applied after a non-rate-limit failure (timeout, 5xx, connection
# reset). Unlike a 429, this says nothing about the model's token quota, so
# a short cooldown -- just enough to not hammer a flaky endpoint -- is right.
_SHORT_COOLDOWN_SECONDS = 10.0

# ASSISTANT_FALLBACK_MODELS default: other chat models available on the same
# Groq key, each with its own separate tokens-per-minute bucket from the
# primary chat model.
_DEFAULT_FALLBACK_MODELS = "openai/gpt-oss-20b,openai/gpt-oss-120b"

# Groq's own duration format for its x-ratelimit-reset-* headers: some
# combination of "<minutes>m", "<seconds>s", "<millis>ms", e.g. "262ms",
# "7.66s", "1m26.4s". (Not to be confused with the standard `retry-after`
# header, which is plain seconds.)
_GROQ_DURATION_RE = re.compile(
    r"\A(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+(?:\.\d+)?)s)?(?:(?P<millis>\d+(?:\.\d+)?)ms)?\Z"
)


def _parse_groq_duration(value: str) -> float | None:
    """Parse a Groq rate-limit-reset header value ("262ms", "7.66s",
    "1m26.4s") into seconds. Returns None for anything that doesn't match --
    a header format change should degrade to the default cooldown, not
    crash the request."""
    if not value:
        return None
    match = _GROQ_DURATION_RE.match(value.strip())
    if not match or not any(match.groups()):
        return None
    minutes, seconds, millis = match.groups()
    total = 0.0
    if minutes:
        total += int(minutes) * 60
    if seconds:
        total += float(seconds)
    if millis:
        total += float(millis) / 1000.0
    return total


def _retry_after_from_headers(headers) -> float:
    """Best-effort wait time (seconds) before a rate-limited model should be
    tried again. Prefers the standard `retry-after` header (seconds); when
    that's absent, Groq's own `x-ratelimit-reset-tokens` /
    `x-ratelimit-reset-requests` headers say when each of the two quotas
    resets -- since either one blocking the request is enough to 429, the
    request only really clears once *both* have reset, so we wait for the
    longer of the two. Falls back to a fixed default when nothing parses."""
    if headers is not None:
        raw = headers.get("retry-after")
        if raw:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
        candidates = [
            parsed
            for parsed in (
                _parse_groq_duration(headers.get("x-ratelimit-reset-tokens") or ""),
                _parse_groq_duration(headers.get("x-ratelimit-reset-requests") or ""),
            )
            if parsed is not None
        ]
        if candidates:
            return max(candidates)
    return _DEFAULT_RETRY_AFTER_SECONDS


class _CooldownRegistry:
    """Thread-safe, in-process map of model name -> (monotonic deadline,
    reason). A model that just came back rate-limited or just failed is
    skipped on sight by the next request instead of spending a network
    round-trip to rediscover a problem we already know about. Deliberately
    process-local and unpersisted: it's a fast-path optimization, not a
    source of truth, and a process restart naturally clearing it is fine.

    The reason ("rate_limit" or "failure") exists so `FallbackBackend` can
    tell a real 429 apart from a plain connectivity/timeout blip even when
    it never attempted the model itself this call, because it was already
    cooling down from an *earlier* call -- see the red-cell finding this
    fixes (M3): without it, "every model happens to be in a short failure
    cooldown right now" and "every model is genuinely rate-limited" were
    indistinguishable from the skip path, and both got reported to the
    visitor as "busy, try again shortly" even when the true cause was a
    Groq outage with no quota question at all."""

    def __init__(self):
        self._lock = threading.Lock()
        self._deadlines: dict[str, float] = {}
        self._reasons: dict[str, str] = {}

    def set_busy(self, model: str, seconds: float, *, reason: str = "rate_limit") -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        with self._lock:
            # Never shorten an existing cooldown: a second signal for a
            # model already cooling down means the first estimate was
            # optimistic, not that the model is about to recover sooner.
            # The reason updates in lockstep with the deadline -- only when
            # this call actually wins does it get to say why.
            if deadline > self._deadlines.get(model, 0.0):
                self._deadlines[model] = deadline
                self._reasons[model] = reason

    def remaining(self, model: str) -> float:
        with self._lock:
            deadline = self._deadlines.get(model)
        if deadline is None:
            return 0.0
        return max(0.0, deadline - time.monotonic())

    def is_busy(self, model: str) -> bool:
        return self.remaining(model) > 0.0

    def reason(self, model: str) -> str | None:
        """Why `model` is currently cooling down, or None if it isn't (or
        never has been). Callers that already checked `remaining() > 0`
        get a meaningful answer; a caller that didn't gets None once the
        deadline has actually passed, rather than a stale reason from a
        cooldown that's long since expired."""
        with self._lock:
            deadline = self._deadlines.get(model)
            if deadline is None or deadline <= time.monotonic():
                return None
            return self._reasons.get(model)


# One shared registry per process -- every GroqBackend/FallbackBackend
# instance (there can be several, one per api_key/model cache entry) reports
# into and reads from the same cooldown state.
_COOLDOWNS = _CooldownRegistry()


class FakeBackend:
    name = "fake"

    def generate(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        tools=None,
        tool_choice=None,
    ) -> LLMReply:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        has_context = "no relevant passages" not in system
        body = (
            f"[fake-backend] question={user.strip()[:200]!r} "
            f"context={'present' if has_context else 'empty'} "
            f"history_msgs={sum(1 for m in messages if m['role'] != 'system') - 1}"
        )
        return LLMReply(text=body, model="fake")


class GroqBackend:
    name = "groq"

    def __init__(
        self,
        api_key: str,
        model: str,
        reasoning_effort: str | None = None,
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
    ):
        try:
            from groq import Groq
        except ImportError as exc:  # pragma: no cover - prod has it
            raise AssistantUnavailable("the groq client is not installed") from exc
        # timeout/max_retries are forwarded only when explicitly given, so
        # existing callers (job_discovery, family chat) that construct
        # GroqBackend(api_key, model[, reasoning_effort]) get byte-identical
        # Groq(api_key=...) construction -- the SDK's own defaults (60s read
        # timeout, 2 retries) still apply for them. The assistant's own
        # build_backend() passes both explicitly, short, for the public chat
        # path: a 124s hang (60s timeout x up to 2 retries plus overhead) was
        # observed holding one of gunicorn's few threads captive.
        client_kwargs = {"api_key": api_key}
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        if max_retries is not None:
            client_kwargs["max_retries"] = max_retries
        self._client = Groq(**client_kwargs)
        self._model = model
        # gpt-oss models reason before answering, and hidden reasoning
        # still counts against max_tokens: at the default ("medium") a
        # 300-token Job Discovery score spent 298 tokens reasoning and
        # returned empty content (measured live 2026-09-23). "low" answered
        # the same prompt in ~150 tokens. None leaves the model default.
        self._reasoning_effort = reasoning_effort

    def generate(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        tools=None,
        tool_choice=None,
    ) -> LLMReply:
        # Low, not zero: a grounded RAG answer should be near-deterministic,
        # but a touch of variation keeps the voice from sounding canned.
        kwargs = dict(
            model=self._model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.1,
        )
        # Only widen the request when there are tools to offer -- a plain
        # chat completion is byte-identical to before this change.
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        reasoning = {"reasoning_format": "hidden"}
        if self._reasoning_effort:
            reasoning["reasoning_effort"] = self._reasoning_effort
        try:
            from groq import BadRequestError, RateLimitError
        except ImportError as exc:  # pragma: no cover - prod has it
            raise AssistantUnavailable("the groq client is not installed") from exc

        # reasoning_format="hidden": ask Groq to drop the <think> block for
        # reasoning models. Falls back to a plain request ONLY on a 400
        # BadRequestError -- the actual "this model/endpoint doesn't accept
        # reasoning_format/reasoning_effort" case the retry exists for.
        # Every other failure fails straight to AssistantUnavailable with no
        # second call: a 429 must not double-spend against an
        # already-exhausted quota, and a timeout/connection error/5xx is
        # certain to fail the same way again, so retrying it just doubles
        # the wait -- measured worst case, a 15s per-call timeout x 2
        # attempts x 3 models in the fallback chain was ~90s of a request
        # thread blocked before this fix.
        try:
            resp = self._client.chat.completions.create(**reasoning, **kwargs)
        except RateLimitError as exc:
            raise self._busy(exc) from exc
        except BadRequestError:
            try:
                resp = self._client.chat.completions.create(**kwargs)
            except RateLimitError as exc:
                raise self._busy(exc) from exc
            except Exception as exc:  # noqa: BLE001 - provider errors are opaque; wrap them
                raise AssistantUnavailable(f"groq request failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - timeout/connection/5xx etc.; no retry
            raise AssistantUnavailable(f"groq request failed: {exc}") from exc

        try:
            choice = resp.choices[0]
            message = choice.message
        except (AttributeError, IndexError) as exc:
            raise AssistantUnavailable("groq returned an unexpected response shape") from exc

        # A tool-call turn legitimately has content=None -- that must not
        # read as a malformed response.
        text = _strip_reasoning(getattr(message, "content", None) or "")
        raw_calls = getattr(message, "tool_calls", None) or []
        tool_calls = tuple(
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": tc.function.arguments or "{}",
            }
            for tc in raw_calls
        ) or None

        usage = getattr(resp, "usage", None)
        return LLMReply(
            text=text,
            model=self._model,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            tool_calls=tool_calls,
            finish_reason=getattr(choice, "finish_reason", None),
        )

    def _busy(self, exc) -> AssistantBusy:
        """Map a groq.RateLimitError to AssistantBusy, parsing whatever
        wait-time header is available, and register this model's cooldown
        so the fallback chain (and any future call to this same backend)
        skips it without another round trip."""
        headers = getattr(getattr(exc, "response", None), "headers", None)
        retry_after = _retry_after_from_headers(headers)
        _COOLDOWNS.set_busy(self._model, retry_after)
        return AssistantBusy(
            f"groq rate-limited model {self._model!r}", retry_after=retry_after
        )


# gpt-oss models reason before answering, and hidden reasoning still counts
# against max_tokens (see GroqBackend's own comment) -- every gpt-oss model
# offered as a fallback gets "low" reasoning effort for the same reason.
def _wants_low_reasoning(model: str) -> bool:
    return model.startswith("openai/gpt-oss")


class FallbackBackend:
    """Tries an ordered chain of Groq models until one answers, instead of
    surfacing the first model's rate limit straight to the visitor.

    Each model on a Groq key has its own separate tokens-per-minute bucket,
    so a 429 on the primary chat model (qwen/qwen3.8-27b: 8,000 tokens/min
    on this key) doesn't mean the account is out of budget -- it means the
    *model* is, and another model can very plausibly still answer. A model
    that comes back rate-limited is recorded in the shared cooldown
    registry (see `_COOLDOWNS`) and skipped on sight by every subsequent
    call until its deadline passes, so the chain doesn't spend a network
    round trip rediscovering a 429 it already knows about.

    A non-rate-limit failure (timeout, 5xx, connection reset) also moves on
    to the next model, but earns only a short cooldown: that kind of error
    says nothing about token quota, so retrying that model again soon is
    reasonable and likely to just work.

    `generate()` has the exact same signature as every other backend, so
    the orchestrator and tests never need to know a chain is involved.
    """

    name = "groq"

    def __init__(
        self,
        api_key: str,
        models: list[str],
        *,
        timeout: float | None = None,
        max_retries: int | None = None,
    ):
        # Preserve order, drop duplicates -- ASSISTANT_FALLBACK_MODELS could
        # repeat the primary model or repeat itself.
        seen: set[str] = set()
        chain: list[str] = []
        for model in models:
            if model and model not in seen:
                seen.add(model)
                chain.append(model)
        if not chain:
            raise AssistantUnavailable("no usable Groq model configured")
        self._chain = chain
        self._backends = {
            model: GroqBackend(
                api_key,
                model,
                reasoning_effort="low" if _wants_low_reasoning(model) else None,
                timeout=timeout,
                max_retries=max_retries,
            )
            for model in chain
        }

    def generate(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        tools=None,
        tool_choice=None,
    ) -> LLMReply:
        best_wait: float | None = None
        last_exc: Exception | None = None
        # True only once something in THIS call actually indicated a quota
        # problem -- a live 429, or a skip against a cooldown some earlier
        # 429 set. A skip against a failure-reason cooldown, or a fresh
        # non-429 failure, never sets this. Whether the visitor is told
        # "busy, try again shortly" (AssistantBusy) or "offline"
        # (AssistantUnavailable) hinges entirely on this flag -- see
        # _CooldownRegistry's docstring for the red-cell finding it fixes.
        saw_rate_limit = False
        for index, model in enumerate(self._chain):
            remaining = _COOLDOWNS.remaining(model)
            if remaining > 0:
                if _COOLDOWNS.reason(model) == "rate_limit":
                    saw_rate_limit = True
                    best_wait = remaining if best_wait is None else min(best_wait, remaining)
                # else: cooling down from an earlier plain failure, not a
                # quota signal -- skip it (don't hammer a flaky model), but
                # don't let a reliability blip masquerade as a busy signal.
                continue
            try:
                reply = self._backends[model].generate(
                    messages, max_tokens=max_tokens, tools=tools, tool_choice=tool_choice
                )
            except AssistantBusy as exc:
                saw_rate_limit = True
                wait = exc.retry_after if exc.retry_after is not None else _COOLDOWNS.remaining(model)
                best_wait = wait if best_wait is None else min(best_wait, wait)
                last_exc = exc
                continue
            except AssistantUnavailable as exc:
                # Not a quota signal (timeout, 5xx, connection) -- don't let
                # one flaky model block the whole chain, but don't hammer it
                # either, and don't let it count toward "busy" below.
                _COOLDOWNS.set_busy(model, _SHORT_COOLDOWN_SECONDS, reason="failure")
                last_exc = exc
                continue
            return reply if index == 0 else replace(reply, fell_back=True)

        if saw_rate_limit:
            raise AssistantBusy(
                "every configured Groq model is rate-limited",
                retry_after=best_wait if best_wait is not None else _DEFAULT_RETRY_AFTER_SECONDS,
            ) from last_exc
        # Every model failed, and none of those failures was a rate limit --
        # this is an outage (Groq down, every model timing out, ...), not a
        # busy signal, so it gets the plain offline treatment instead of a
        # misleading "try again in a few seconds".
        raise AssistantUnavailable(
            "every configured Groq model failed (not a rate limit)"
        ) from last_exc


class ScriptedBackend:
    """Test-only backend that replays a queue of pre-programmed turns so a
    test can drive the tool-calling loop deterministically.

    Push turns with `push(...)`; each `generate()` pops the next one. A turn
    is `{"text": "..."}` for a plain answer or
    `{"tool_calls": [{"id", "name", "arguments"}, ...]}` (optionally also
    "text") for a tool-call turn. `arguments` is a JSON string, matching the
    provider wire format. Running out of script returns an empty final
    reply. `generate()` records its arguments on `calls` for assertions.
    """

    name = "scripted"

    def __init__(self):
        self.script: list[dict] = []
        self.calls: list[dict] = []

    def reset(self):
        self.script.clear()
        self.calls.clear()

    def push(self, *turns: dict):
        self.script.extend(turns)

    def generate(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        tools=None,
        tool_choice=None,
    ) -> LLMReply:
        self.calls.append(
            {"messages": list(messages), "tools": tools, "tool_choice": tool_choice}
        )
        turn = self.script.pop(0) if self.script else {"text": ""}
        raw_calls = turn.get("tool_calls")
        tool_calls = tuple(dict(tc) for tc in raw_calls) if raw_calls else None
        return LLMReply(
            text=turn.get("text", ""),
            model="scripted",
            prompt_tokens=turn.get("prompt_tokens", 1),
            completion_tokens=turn.get("completion_tokens", 1),
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
        )


# One shared instance so a test can reach it via
# `from app.services.assistant.backends import SCRIPTED_BACKEND`.
SCRIPTED_BACKEND = ScriptedBackend()

_CACHE: dict[tuple, object] = {}


def build_backend(config, *, for_tools: bool = False):
    """Return the configured backend. `for_tools=True` (the assistant's own
    chat path -- it always calls this with `for_tools=True`, since the same
    turn may or may not end up using tools -- and the owner's authorized
    job-tracker path) selects `GROQ_TOOL_MODEL` when set -- a model chosen
    for reliable function-calling -- falling back to the chat model
    otherwise, then chains `ASSISTANT_FALLBACK_MODELS` behind it so a 429 on
    the primary model doesn't have to reach the visitor. `for_tools=False`
    (currently only `backend_available()`'s cheap existence check) builds
    the same wrapper around the primary model alone -- no fallback chain,
    since nothing on that path actually calls `generate()`.

    Both paths apply `ASSISTANT_GROQ_TIMEOUT_SECONDS` / `_MAX_RETRIES` to
    every model in the chain: short and zero by default, because the SDK's
    own defaults (60s read timeout, 2 retries) can hold one of the app's
    handful of worker threads for minutes on a single bad turn (a 124s hang
    ending in 429 was observed in production logs)."""
    kind = config["ASSISTANT_LLM_BACKEND"]

    if kind == "fake":
        return FakeBackend()

    if kind == "scripted":  # test-only, same footing as "fake"
        return SCRIPTED_BACKEND

    if kind == "groq":
        api_key = config.get("GROQ_API_KEY") or ""
        if not api_key:
            raise AssistantUnavailable("GROQ_API_KEY is not configured")
        model = config["GROQ_MODEL"]
        if for_tools:
            model = config.get("GROQ_TOOL_MODEL") or model

        timeout = config.get("ASSISTANT_GROQ_TIMEOUT_SECONDS", 15)
        max_retries = config.get("ASSISTANT_GROQ_MAX_RETRIES", 0)

        if for_tools:
            fallback_raw = config.get("ASSISTANT_FALLBACK_MODELS", _DEFAULT_FALLBACK_MODELS) or ""
            chain = [model] + [m.strip() for m in fallback_raw.split(",") if m.strip()]
        else:
            chain = [model]

        # api_key is part of the cache key, not just (kind, chain): two
        # callers building the same model with two different keys (e.g. the
        # main /assistant chat vs. a feature given its own dedicated Groq
        # quota) must never share a cached client, or the second caller's
        # requests would silently bill against the first caller's key
        # instead of its own. timeout/max_retries are in the key too, since
        # a future caller asking for different limits on the same
        # model/key must not get someone else's client back.
        cache_key = ("groq", tuple(chain), api_key, timeout, max_retries)
        if cache_key not in _CACHE:
            _CACHE[cache_key] = FallbackBackend(
                api_key, chain, timeout=timeout, max_retries=max_retries
            )
        return _CACHE[cache_key]

    raise AssistantUnavailable(f"unknown ASSISTANT_LLM_BACKEND {kind!r}")


def backend_available(config) -> bool:
    """Cheap check for the page: can a backend be built at all? (Does not
    make a network call.)"""
    try:
        build_backend(config)
        return True
    except AssistantUnavailable:
        return False

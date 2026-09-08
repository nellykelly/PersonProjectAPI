"""LLM generation backends behind one interface.

`GroqBackend` calls Groq's OpenAI-shaped chat API. `FakeBackend` is
deterministic and offline -- the test suite and any run with
`ASSISTANT_LLM_BACKEND=fake` use it. `ScriptedBackend` is a test-only
backend that replays a queue of pre-programmed turns, used to drive the
tool-calling loop deterministically. A future Phase 4 `OllamaBackend`
drops in here with no change above this module.

The one method every backend exposes is
`generate(messages, *, max_tokens, tools=None, tool_choice=None) -> LLMReply`.
`tools` is passed only on the owner's authorized job-tracker path; on every
other call it is `None` and the request is identical to a plain chat
completion.

Any provider failure -- missing key, network, rate limit, bad response --
becomes `AssistantUnavailable`, so the route can return a clean 503 and
never leaks a raw provider error to the browser.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import AssistantUnavailable

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

    def __init__(self, api_key: str, model: str):
        try:
            from groq import Groq
        except ImportError as exc:  # pragma: no cover - prod has it
            raise AssistantUnavailable("the groq client is not installed") from exc
        self._client = Groq(api_key=api_key)
        self._model = model

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
        try:
            # reasoning_format="hidden": ask Groq to drop the <think> block
            # for reasoning models. Falls back if the model/endpoint
            # doesn't accept the param.
            try:
                resp = self._client.chat.completions.create(
                    reasoning_format="hidden", **kwargs
                )
            except Exception:  # noqa: BLE001 - unknown-param etc.; retry plain
                resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - provider errors are opaque; wrap them
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
    """Return the configured backend. `for_tools=True` (the owner's
    authorized job-tracker path) selects `GROQ_TOOL_MODEL` when set -- a
    model chosen for reliable function-calling -- falling back to the chat
    model otherwise."""
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
        cache_key = ("groq", model)
        if cache_key not in _CACHE:
            _CACHE[cache_key] = GroqBackend(api_key, model)
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

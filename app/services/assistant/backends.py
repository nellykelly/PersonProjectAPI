"""LLM generation backends behind one interface.

`GroqBackend` calls Groq's OpenAI-shaped chat API (Llama 3.3 70B on the
free tier). `FakeBackend` is deterministic and offline -- the test suite
and any run with `ASSISTANT_LLM_BACKEND=fake` use it. A future Phase 4
`OllamaBackend` drops in here with no change above this module.

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


class FakeBackend:
    name = "fake"

    def generate(self, messages: list[dict], *, max_tokens: int) -> LLMReply:
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

    def generate(self, messages: list[dict], *, max_tokens: int) -> LLMReply:
        # Low, not zero: a grounded RAG answer should be near-deterministic,
        # but a touch of variation keeps the voice from sounding canned.
        kwargs = dict(
            model=self._model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.1,
        )
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
            text = _strip_reasoning(resp.choices[0].message.content or "")
        except (AttributeError, IndexError) as exc:
            raise AssistantUnavailable("groq returned an unexpected response shape") from exc

        usage = getattr(resp, "usage", None)
        return LLMReply(
            text=text,
            model=self._model,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )


_CACHE: dict[tuple, object] = {}


def build_backend(config):
    kind = config["ASSISTANT_LLM_BACKEND"]

    if kind == "fake":
        return FakeBackend()

    if kind == "groq":
        api_key = config.get("GROQ_API_KEY") or ""
        model = config["GROQ_MODEL"]
        if not api_key:
            raise AssistantUnavailable("GROQ_API_KEY is not configured")
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

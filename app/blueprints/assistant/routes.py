"""The personal AI assistant: a dedicated page and one JSON chat endpoint.

`GET /assistant` renders the chat page (or a calm "offline" panel when no
backend is configured). `POST /api/assistant/chat` runs one RAG turn.

Security posture:
- per-IP rate limit, hard input-length cap, server-side history truncation
- the system prompt treats retrieved text and history as data, not
  instructions
- the job-tracker tools are handed to the model only when
  `can_use_job_tools()` holds -- the owner is signed in AND the
  /job-tracker gate is unlocked this session; for anyone else the tool
  schemas are never built, so there is nothing to escalate to
- NOT csrf-exempt: the fetch() sends an X-CSRFToken header
- every call is written to `assistant_queries` (question truncated, IP only
  ever stored hashed)
- any dependency failure -> a clean 503 with a friendly body, never a trace
"""
from __future__ import annotations

import hashlib
import time

from flask import current_app, jsonify, render_template, request, url_for

from app.blueprints.assistant import bp
from app.extensions import db, limiter
from app.models import AssistantQuery
from app.services import assistant
from app.services.assistant.authz import can_use_job_tools, is_owner

_QUESTION_LOG_MAX = 500
_OFFLINE_MESSAGE = (
    "The assistant is unavailable right now. Please try again later, or email "
    "koskela.nelson@gmail.com."
)

# Where each content source actually lives on the site, so a citation in
# the chat can be a real link. Keys are ContentChunk.source values (the
# Markdown file paths under app/assistant_content/); a source with no
# entry here renders as plain text.
_SOURCE_ENDPOINTS = {
    "bio": "about.index",
    "faq": "about.index",
    "projects/trading-simulator": "trading.index",
    "projects/company-scorer": "qr.index",
    "projects/pipeline-world": "pipeline_world.index",
    "projects/sre-infra": "sre_infra.index",
    "projects/site-traffic": "sniffer.index",
    "projects/timed-squares": "timed_squares.index",
    "projects/beeznest": "projects.index",
}


def _source_url(source: str) -> str | None:
    endpoint = _SOURCE_ENDPOINTS.get(source)
    if not endpoint:
        return None
    try:
        return url_for(endpoint)
    except Exception:  # noqa: BLE001 - a missing endpoint just means no link
        return None


def _ip_hash() -> str | None:
    ip = request.remote_addr
    if not ip:
        return None
    salt = current_app.config.get("SECRET_KEY", "")
    return hashlib.sha256(f"{ip}{salt}".encode("utf-8")).hexdigest()


# The owner check lives in app.services.assistant.authz now, so the route
# and the orchestrator's tool layer share one implementation. Kept under
# the old name because `_is_admin()` is what the logging path below reads.
_is_admin = is_owner


@bp.route("/assistant", methods=["GET"])
def index():
    return render_template(
        "assistant/index.html",
        assistant_online=assistant.backend_available(current_app.config),
    )


@bp.route("/assistant/stats", methods=["GET"])
def stats():
    """Public, aggregate-only usage stats for the assistant. Built by a
    plain query over `assistant_queries` -- the per-row heuristic signals
    were written at chat time, so this page runs no model and costs no
    tokens (see app/services/assistant/analytics.py)."""
    from app.services.assistant.analytics import compute_stats

    return render_template("assistant/stats.html", stats=compute_stats(days=30))


@bp.route("/api/assistant/chat", methods=["POST"])
@limiter.limit(lambda: current_app.config["ASSISTANT_CHAT_RATE_LIMIT"])
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get("message")
    history = data.get("history", [])
    is_admin = _is_admin()
    job_tools_ok = can_use_job_tools()

    started = time.monotonic()
    try:
        result = assistant.answer(
            message,
            history,
            config=current_app.config,
            is_admin=is_admin,
            job_tools_authorized=job_tools_ok,
        )
    except assistant.AssistantInputError as exc:
        return jsonify({"reply": str(exc), "error": True}), 400
    except assistant.AssistantUnavailable as exc:
        _log(
            question=message,
            is_admin=is_admin,
            latency_ms=int((time.monotonic() - started) * 1000),
            error=str(exc),
        )
        return jsonify({"reply": _OFFLINE_MESSAGE, "error": True}), 503

    _log(
        question=message,
        is_admin=is_admin,
        latency_ms=int((time.monotonic() - started) * 1000),
        backend=result.backend,
        model=result.model,
        n_chunks=result.n_chunks,
        n_sources=len(result.sources),
        prompt_tokens_est=result.prompt_tokens,
        completion_tokens_est=result.completion_tokens,
        reply=result.reply,
    )
    sources = [
        {
            "title": s["title"],
            "label": s["title"].split(":", 1)[-1].strip() if ":" in s["title"] else s["title"],
            "source": s["source"],
            "url": _source_url(s["source"]),
        }
        for s in result.sources
    ]
    return jsonify(
        {
            "reply": result.reply,
            "sources": sources,
            "backend": result.backend,
            "error": False,
        }
    )


_REPLY_LOG_MAX = 800


def _log(*, question, is_admin, latency_ms, backend=None, model=None, n_chunks=None,
         n_sources=None, prompt_tokens_est=None, completion_tokens_est=None,
         reply=None, error=None) -> None:
    from app.services.assistant.analytics import classify_message, classify_reply

    q = question if isinstance(question, str) else ""
    r = reply if isinstance(reply, str) else ""
    try:
        signals = classify_message(q)
        row = AssistantQuery(
            ip_hash=_ip_hash(),
            is_admin=is_admin,
            backend=backend,
            model=model,
            latency_ms=latency_ms,
            prompt_tokens_est=prompt_tokens_est,
            completion_tokens_est=completion_tokens_est,
            n_chunks=n_chunks,
            n_sources=n_sources,
            question=q[:_QUESTION_LOG_MAX] or None,
            reply=r[:_REPLY_LOG_MAX] or None,
            error=(error or None),
            word_count=signals["word_count"],
            sentiment=signals["sentiment"],
            is_frustrated=signals["is_frustrated"],
            profanity_count=signals["profanity_count"],
            category=signals["category"],
            reply_kind=classify_reply(r, error=bool(error)),
        )
        db.session.add(row)
        db.session.commit()
    except Exception:  # noqa: BLE001 - logging must never break the response
        db.session.rollback()

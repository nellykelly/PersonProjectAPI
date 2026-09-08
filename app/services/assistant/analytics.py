"""Token-free analytics for the assistant.

Two halves:

* `classify_message` / `classify_reply` -- cheap lexicon + regex heuristics
  run once per chat turn at log time (see routes.py::_log). No LLM, no
  network, sub-millisecond, so the chat path is unaffected.
* `compute_stats` -- a plain aggregation over the `assistant_queries`
  rows those heuristics populated. Rendered by `/assistant/stats`.

Nothing here ever calls a model. Every signal is a keyword hit, a regex,
or a count.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import timedelta

from app.extensions import db
from app.models import (
    ASSISTANT_MESSAGE_CATEGORIES,
    ASSISTANT_REPLY_KINDS,
    ASSISTANT_SENTIMENTS,
    AssistantQuery,
    utcnow,
)
from app.services.net_monitor import _percentile
from app.services.validators import _BLOCKED_NAME_HASHES

# --------------------------------------------------------------------------
# lexicons (small on purpose -- this is a vibe check, not real NLP)
# --------------------------------------------------------------------------

_POSITIVE = {
    "thanks", "thank", "thankyou", "great", "awesome", "cool", "nice", "helpful",
    "perfect", "love", "amazing", "excellent", "good", "brilliant", "wonderful",
    "appreciate", "appreciated", "clear", "impressive", "solid", "neat", "sweet",
    "fantastic", "wow", "yay",
}
_NEGATIVE = {
    "useless", "stupid", "dumb", "terrible", "awful", "bad", "wrong", "broken",
    "hate", "worst", "annoying", "unhelpful", "confusing", "confused",
    "frustrating", "frustrated", "garbage", "trash", "pointless", "ridiculous",
    "nonsense", "lame", "sucks", "suck", "disappointing", "unclear", "vague",
    "nope", "no",
}

_FRUSTRATION_PHRASES = (
    "not helpful", "doesn't help", "didn't help", "does not help",
    "you already said", "i already asked", "already told you", "already asked",
    "that's not what i asked", "thats not what i asked", "not what i asked",
    "just answer", "answer the question", "answer my question",
    "stop repeating", "you keep saying", "are you broken", "is this broken",
    "this is useless", "come on", "for real", "seriously",
    "read the question", "listen to me", "pay attention",
)
_REPEAT_PUNCT = re.compile(r"[?!]{3,}")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*")
_ALPHA_RE = re.compile(r"[a-z]+")

# reply-kind markers, checked against the assistant's own text
_GAP_MARKERS = (
    "not something he's written up here", "not something he has written up here",
    "that's not in anything", "thats not in anything", "not in anything he",
    "not in the material", "don't have that information",
    "do not have that information", "the site doesn't cover",
    "the site does not cover", "nothing on the site", "nothing here on that",
    "isn't on the site", "not on the site", "no mention of",
)
_REDIRECT_MARKERS = (
    "outside what i", "not what i'm here for", "not what i am here for",
    "don't do general", "do not do general", "outside my remit",
    "a bit outside", "that's outside", "thats outside", "i only really cover",
    "i'm really just here", "im really just here",
)
_REFUSAL_MARKERS = (
    "not something i'm going to do", "not something i am going to do",
    "can't do that", "cannot do that", "won't share", "will not share",
    "not going to reveal", "i can't put words in his mouth",
    "i cannot put words in his mouth",
)

# category routing (first match wins; order matters)
_CATEGORY_KEYWORDS = (
    ("meta_bot", (
        "who are you", "what are you", "your name", "are you hera", "are you a bot",
        "are you ai", "which model", "what model", "system prompt", "your prompt",
        "how do you work", "who made you", "who built you",
    )),
    ("contact_availability", (
        "contact", "reach him", "reach nelson", "get in touch", "email",
        "hire", "hiring", "available", "availability", "relocat", "remote",
        "salary", "compensation", "rate", "start date", "notice period",
        "open to", "looking for work", "job", "role", "position", "opportunity",
    )),
    ("project_specific", (
        "pipeline world", "pipeline-world", "trading simulator", "trading sim",
        "sre infra", "infra layer", "site traffic", "network sniffer",
        "timed squares", "timed-squares", "beeznest", "company scorer",
        "qr scorer", "leetcode", "the assistant", "this chatbot", "redis",
        "postgres", "pgvector", "docker", "kubernetes", "k8s", "socket",
        "black-scholes", "black scholes", "edgar", "yfinance", "rq worker",
        "cache-aside", "caddy", "gunicorn", "the pipeline", "the game",
    )),
    ("skills_experience", (
        "experience with", "experience in", "does he know", "know how to",
        "familiar with", "worked with", "years of", "how many years",
        "skill", "tech stack", "his stack", "language", "framework",
        "proficient", "expert", "can he", "has he used", "background in",
        "good at", "strong in", "specialise", "specialize",
    )),
    ("about_bio", (
        "who is nelson", "about nelson", "his background", "where did he",
        "where does he", "school", "study", "studied", "degree", "university",
        "college", "jpmorgan", "jpmc", "chase", "career", "how long has he",
        "based", "located", "from",
    )),
)

_GREETINGS = {
    "hi", "hey", "hello", "yo", "sup", "hiya", "howdy", "hii", "heya",
    "hey there", "hi there", "good morning", "good afternoon", "good evening",
    "gm", "wsup", "whats up", "what's up",
}

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being", "am",
    "he", "him", "his", "she", "her", "they", "them", "it", "its", "i", "me",
    "my", "we", "us", "our", "you", "your", "yours", "this", "that", "these",
    "those", "of", "to", "in", "on", "for", "and", "or", "but", "with", "about",
    "as", "at", "by", "from", "into", "out", "up", "down", "over", "under",
    "do", "does", "did", "doing", "done", "have", "has", "had", "having",
    "what", "whats", "which", "who", "whom", "whose", "when", "where", "why",
    "how", "can", "could", "would", "should", "will", "shall", "may", "might",
    "must", "not", "no", "yes", "if", "then", "than", "so", "just", "any",
    "some", "more", "most", "much", "many", "there", "here", "tell", "know",
    "get", "got", "make", "made", "give", "want", "need", "like", "also",
    "really", "very", "please", "thanks", "thank", "ok", "okay", "hi", "hey",
    "hello", "nelson", "he's", "hes", "im", "i'm", "ive", "i've", "dont",
    "don't", "cant", "can't", "wont", "won't", "isnt", "isn't", "youre",
    "you're", "about", "work", "worked", "project", "projects",
}


# --------------------------------------------------------------------------
# per-turn classification (log time)
# --------------------------------------------------------------------------


def _profanity_count(text: str) -> int:
    lowered = (text or "").lower()
    return sum(
        1
        for tok in _ALPHA_RE.findall(lowered)
        if hashlib.sha256(tok.encode("utf-8")).hexdigest() in _BLOCKED_NAME_HASHES
    )


def _sentiment(tokens: list[str], text: str) -> str:
    pos = sum(1 for t in tokens if t in _POSITIVE)
    neg = sum(1 for t in tokens if t in _NEGATIVE)
    # a lone "no"/"nope" as the whole message reads as negative; buried in a
    # sentence it usually doesn't
    if len(tokens) > 4:
        neg -= sum(1 for t in tokens if t in {"no", "nope"})
        neg = max(neg, 0)
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


def _caps_ratio(text: str) -> float:
    words = re.findall(r"[A-Za-z]{3,}", text or "")
    if len(words) < 3:
        return 0.0
    shouted = sum(1 for w in words if w.isupper())
    return shouted / len(words)


def _is_frustrated(text: str, tokens: list[str], sentiment: str, profanity: int) -> bool:
    lowered = (text or "").lower()
    if any(p in lowered for p in _FRUSTRATION_PHRASES):
        return True
    if _REPEAT_PUNCT.search(text or ""):
        return True
    if _caps_ratio(text) >= 0.6:
        return True
    if sentiment == "negative" and (
        any(t in _NEGATIVE for t in tokens[:6]) or len(tokens) <= 6
    ):
        return True
    if profanity > 0 and len(tokens) <= 8:
        return True
    return False


def _category(text: str, tokens: list[str]) -> str:
    lowered = (text or "").strip().lower()
    stripped = lowered.rstrip("?!. ")
    if stripped in _GREETINGS or (len(tokens) <= 3 and any(t in _GREETINGS for t in tokens)):
        return "greeting"
    for name, keywords in _CATEGORY_KEYWORDS:
        if any(k in lowered for k in keywords):
            return name
    return "off_topic"


def classify_message(text: str) -> dict:
    """Everything we record about the *user's* message. Cheap by design."""
    text = text or ""
    tokens = _WORD_RE.findall(text.lower())
    sentiment = _sentiment(tokens, text)
    profanity = _profanity_count(text)
    return {
        "word_count": len(tokens),
        "sentiment": sentiment,
        "profanity_count": profanity,
        "is_frustrated": _is_frustrated(text, tokens, sentiment, profanity),
        "category": _category(text, tokens),
    }


def classify_reply(reply: str, *, error: bool) -> str:
    """What kind of answer the assistant gave -- markers only, no model."""
    if error:
        return "error"
    low = (reply or "").lower()
    if any(m in low for m in _REFUSAL_MARKERS):
        return "refused"
    if any(m in low for m in _GAP_MARKERS):
        return "unanswered_gap"
    if any(m in low for m in _REDIRECT_MARKERS):
        return "redirect_offtopic"
    return "answered"


# --------------------------------------------------------------------------
# aggregation (page time)
# --------------------------------------------------------------------------

_QUESTION_NORM_RE = re.compile(r"[^a-z0-9\s']")
_TOP_WORDS = 12
_TOP_QUESTIONS = 10
# A question shows in "most asked" only once it's been asked at least this
# many times, and never if any instance of it tripped the profanity
# filter -- so a one-off odd or offensive input can't land on a public
# page. Lower this (to 1) if you'd rather show every distinct question.
_MIN_COUNT_FOR_TOP_QUESTION = 2


def _normalise_question(q: str) -> str:
    q = (q or "").strip().lower()
    q = _QUESTION_NORM_RE.sub(" ", q)
    q = re.sub(r"\s+", " ", q).strip()
    q = re.sub(r"^(so|and|but|ok|okay|hey|hi|hello)\s+", "", q)
    return q


def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def compute_stats(days: int = 30) -> dict:
    since = utcnow() - timedelta(days=days)
    rows = (
        AssistantQuery.query.filter(AssistantQuery.created_at >= since)
        .order_by(AssistantQuery.created_at.asc())
        .all()
    )
    total = len(rows)

    empty = {
        "days": days,
        "total_messages": 0,
        "generated_at": utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }
    if not total:
        return empty

    errors = sum(1 for r in rows if r.error)
    ok = [r for r in rows if not r.error]

    # --- conversation / volume ---
    askers = {r.ip_hash for r in rows if r.ip_hash}
    by_day = Counter(r.created_at.strftime("%Y-%m-%d") for r in rows)
    by_hour = Counter(r.created_at.hour for r in rows)
    volume = [
        {"date": d, "count": by_day.get(d, 0)}
        for d in _date_range(since, utcnow())
    ]
    busiest_hours = [
        {"hour": h, "count": c}
        for h, c in sorted(by_hour.items(), key=lambda kv: kv[1], reverse=True)[:5]
    ]

    # --- latency / cost ---
    lat = [r.latency_ms for r in rows if r.latency_ms is not None]
    latency = {
        "p50": round(_percentile(lat, 50)) if lat else None,
        "p90": round(_percentile(lat, 90)) if lat else None,
        "max": max(lat) if lat else None,
    }
    tokens_in = sum(r.prompt_tokens_est or 0 for r in rows)
    tokens_out = sum(r.completion_tokens_est or 0 for r in rows)

    # --- tone ---
    sent = Counter(r.sentiment for r in rows if r.sentiment)
    sentiment_breakdown = [
        {"label": s, "count": sent.get(s, 0), "pct": _pct(sent.get(s, 0), total)}
        for s in ASSISTANT_SENTIMENTS
    ]
    frustrated = sum(1 for r in rows if r.is_frustrated)
    cursing = sum(1 for r in rows if (r.profanity_count or 0) > 0)
    positive = sent.get("positive", 0)

    # --- what people ask ---
    cat = Counter(r.category for r in rows if r.category)
    category_breakdown = [
        {"label": c, "count": cat.get(c, 0), "pct": _pct(cat.get(c, 0), total)}
        for c in ASSISTANT_MESSAGE_CATEGORIES
        if cat.get(c, 0)
    ]
    category_breakdown.sort(key=lambda x: x["count"], reverse=True)

    kind = Counter(r.reply_kind for r in rows if r.reply_kind)
    reply_breakdown = [
        {"label": k, "count": kind.get(k, 0), "pct": _pct(kind.get(k, 0), total)}
        for k in ASSISTANT_REPLY_KINDS
        if kind.get(k, 0)
    ]

    # --- top words (from questions, profane + stopwords removed) ---
    word_counter: Counter = Counter()
    for r in rows:
        if r.profanity_count:
            continue
        for tok in _WORD_RE.findall((r.question or "").lower()):
            if len(tok) < 3 or tok in _STOPWORDS or tok.isdigit():
                continue
            word_counter[tok] += 1
    top_words = [
        {"word": w, "count": c} for w, c in word_counter.most_common(_TOP_WORDS)
    ]

    # --- most asked questions (>= N times, and never if any instance was
    #     profane -- keeps a one-off odd/offensive input off a public page) ---
    q_count: Counter = Counter()
    q_display: dict[str, str] = {}
    q_flagged: set[str] = set()
    for r in rows:
        if not (r.question or "").strip():
            continue
        norm = _normalise_question(r.question)
        if len(norm) < 4:
            continue
        if r.profanity_count:
            q_flagged.add(norm)
            continue
        q_count[norm] += 1
        q_display.setdefault(norm, r.question.strip())
    top_questions = sorted(
        (
            {"question": q_display[n], "count": c}
            for n, c in q_count.items()
            if c >= _MIN_COUNT_FOR_TOP_QUESTION and n not in q_flagged
        ),
        key=lambda x: x["count"],
        reverse=True,
    )[:_TOP_QUESTIONS]

    return {
        "days": days,
        "generated_at": utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "total_messages": total,
        "unique_visitors": len(askers),
        "error_rate": _pct(errors, total),
        "avg_words_per_message": round(
            sum(r.word_count or 0 for r in rows) / total, 1
        ),
        "latency": latency,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "frustrated_count": frustrated,
        "frustrated_pct": _pct(frustrated, total),
        "cursing_count": cursing,
        "cursing_pct": _pct(cursing, total),
        "positive_count": positive,
        "positive_pct": _pct(positive, total),
        "sentiment_breakdown": sentiment_breakdown,
        "category_breakdown": category_breakdown,
        "reply_breakdown": reply_breakdown,
        "top_words": top_words,
        "top_questions": top_questions,
        "volume": volume,
        "busiest_hours": busiest_hours,
        "answered_pct": _pct(kind.get("answered", 0), total),
    }


def _date_range(start, end) -> list[str]:
    days = (end.date() - start.date()).days
    out = []
    d = start.date()
    for _ in range(days + 1):
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out[-60:]  # cap the x-axis

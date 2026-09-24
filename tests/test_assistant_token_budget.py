"""T1 (token diet) -- prompts.build_messages(offered_domains=...) and
retrieval.retrieve's relevance-floor / char-cap / small-talk-skip kwargs.

Token size is estimated as total chars/4 across every message's `content`,
exactly like the battle plan's acceptance numbers -- no real tokenizer, so
this never depends on which backend is configured. Retrieval tests use a
tiny stub store/embedder (never HashEmbedder's actual score distribution)
for the floor/margin/cap behavior, since those numbers must hold regardless
of which embedder produced the scores; the one end-to-end test that does
run through HashEmbedder only checks the *shape* of the result (chunks
found, budget respected), never a specific score.
"""
from __future__ import annotations

import pytest

from app.extensions import db
from app.models import ContentChunk
from app.services.assistant import prompts
from app.services.assistant.chunking import estimate_tokens
from app.services.assistant.embeddings import HashEmbedder
from app.services.assistant.prompts import build_messages
from app.services.assistant.retrieval import retrieve, retrieval_params
from app.services.assistant.store import InMemoryStore, RetrievedChunk


def _est_tokens(messages: list[dict]) -> float:
    return sum(len(m.get("content", "")) for m in messages) / 4


def _rc(source, score, *, content="", title=None):
    return RetrievedChunk(
        source=source, kind="project", title=title or source, chunk_index=0,
        content=content, score=score,
    )


class _StubStore:
    """A VectorStore that hands back a fixed, already-scored chunk list --
    lets the floor/margin/cap tests control scores directly instead of
    depending on any real (or hashing) embedder's output."""

    def __init__(self, chunks):
        self._chunks = chunks

    def search(self, query_embedding, k):
        return self._chunks[:k]


class _StubEmbedder:
    """Records whether it was ever asked to embed -- the small-talk-skip
    tests assert on `calls` to prove the skip happens *before* embedding,
    not just that the result happens to come back empty."""

    def __init__(self):
        self.calls = 0

    def embed_query(self, text):
        self.calls += 1
        return [0.0]


# ---------- build_messages(offered_domains=...) ----------


def test_offered_domains_none_keeps_the_full_legacy_note_byte_identical():
    """Backward compatibility: a caller that hasn't been updated to pass
    `offered_domains` (i.e. every existing call site until Keystone wires
    T1 into orchestrator.py) must get exactly the old prompt."""
    messages = build_messages(question="hi", context_text="", history=[])
    assert prompts._PUBLIC_TOOL_NOTE in messages[0]["content"]


def test_empty_offered_domains_omits_the_public_tool_note_entirely():
    messages = build_messages(
        question="hi", context_text="", history=[], offered_domains=set()
    )
    system = messages[0]["content"]
    assert "public tools" not in system
    assert "confirmation_token" not in system


def test_trading_domain_note_has_confirmation_token_and_chart_rules_but_not_traffic():
    messages = build_messages(
        question="show me the trading sim",
        context_text="",
        history=[],
        offered_domains={"trading"},
    )
    system = messages[0]["content"]
    assert "public tools" in system
    assert "confirmation_token" in system
    assert "preview_" in system
    assert "get_projection" in system
    assert "get_traffic_summary" not in system  # traffic domain not offered


def test_traffic_domain_note_has_chart_rule_but_not_confirmation_token():
    messages = build_messages(
        question="show me the traffic chart",
        context_text="",
        history=[],
        offered_domains={"traffic"},
    )
    system = messages[0]["content"]
    assert "get_traffic_summary" in system
    assert "confirmation_token" not in system  # no preview/confirm tool in this domain


def test_pipeline_domain_note_has_confirmation_token_and_other_visitor_rule():
    messages = build_messages(
        question="join pipeline world",
        context_text="",
        history=[],
        offered_domains={"pipeline"},
    )
    system = messages[0]["content"]
    assert "confirmation_token" in system
    assert "check_character_status" in system


def test_scorer_only_domain_gets_no_confirm_or_chart_rules():
    messages = build_messages(
        question="run the company scorer",
        context_text="",
        history=[],
        offered_domains={"scorer"},
    )
    system = messages[0]["content"]
    assert "Company Scorer" in system
    assert "confirmation_token" not in system
    assert "draws a real chart" not in system


def test_job_tracker_note_unchanged_by_offered_domains():
    """_TOOL_NOTE (the owner job-tracker tools) stays gated on `job_tools`
    only -- offered_domains must never turn it on or off."""
    with_tools = build_messages(
        question="x", context_text="", history=[], job_tools=True, offered_domains=set()
    )
    assert prompts._TOOL_NOTE in with_tools[0]["content"]
    without_tools = build_messages(
        question="x", context_text="", history=[], job_tools=False, offered_domains=set()
    )
    assert prompts._TOOL_NOTE not in without_tools[0]["content"]


def test_no_context_literal_preserved_for_fakebackend():
    messages = build_messages(
        question="x", context_text="", history=[], offered_domains=set()
    )
    assert "no relevant passages" in messages[0]["content"]


# ---------- retrieve(): relevance floor / margin / cap / small-talk skip ----------


def test_min_score_drops_chunks_below_the_absolute_floor():
    chunks = [_rc("a", 0.9), _rc("b", 0.5), _rc("c", 0.2)]
    result = retrieve(
        "q", k=3, embedder=_StubEmbedder(), store=_StubStore(chunks), min_score=0.4
    )
    assert [c.source for c in result.chunks] == ["a", "b"]


def test_score_margin_keeps_only_chunks_near_the_best():
    chunks = [_rc("a", 0.9), _rc("b", 0.85), _rc("c", 0.5)]
    result = retrieve(
        "q", k=3, embedder=_StubEmbedder(), store=_StubStore(chunks), score_margin=0.1
    )
    assert [c.source for c in result.chunks] == ["a", "b"]


def test_min_score_and_margin_combine():
    chunks = [_rc("a", 0.9), _rc("b", 0.85), _rc("c", 0.5), _rc("d", 0.1)]
    result = retrieve(
        "q",
        k=4,
        embedder=_StubEmbedder(),
        store=_StubStore(chunks),
        min_score=0.4,
        score_margin=0.1,
    )
    # min_score drops "d" first, then margin (relative to "a"'s 0.9) drops "c"
    assert [c.source for c in result.chunks] == ["a", "b"]


def test_no_floors_preserves_current_behavior():
    chunks = [_rc("a", 0.9), _rc("b", 0.1)]
    result = retrieve("q", k=2, embedder=_StubEmbedder(), store=_StubStore(chunks))
    assert [c.source for c in result.chunks] == ["a", "b"]


def test_max_context_chars_trims_lowest_scoring_chunks_first():
    chunks = [
        _rc("a", 0.9, content="x" * 100, title="A"),
        _rc("b", 0.8, content="y" * 100, title="B"),
        _rc("c", 0.7, content="z" * 100, title="C"),
    ]
    result = retrieve(
        "q", k=3, embedder=_StubEmbedder(), store=_StubStore(chunks), max_context_chars=150
    )
    assert [c.source for c in result.chunks] == ["a"]
    assert len(result.context_text) <= 150


def test_max_context_chars_can_empty_the_result_if_nothing_fits():
    chunks = [_rc("a", 0.9, content="x" * 500, title="A")]
    result = retrieve(
        "q", k=1, embedder=_StubEmbedder(), store=_StubStore(chunks), max_context_chars=10
    )
    assert result.chunks == []
    assert result.context_text == ""


def test_skip_for_small_talk_returns_empty_without_embedding_a_greeting():
    embedder = _StubEmbedder()
    result = retrieve(
        "hi there",
        k=5,
        embedder=embedder,
        store=_StubStore([_rc("a", 0.9)]),
        skip_for_small_talk=True,
    )
    assert result.chunks == []
    assert result.context_text == ""
    assert embedder.calls == 0


def test_skip_for_small_talk_returns_empty_for_a_meta_bot_question():
    embedder = _StubEmbedder()
    result = retrieve(
        "who are you?",
        k=5,
        embedder=embedder,
        store=_StubStore([_rc("a", 0.9)]),
        skip_for_small_talk=True,
    )
    assert result.chunks == []
    assert embedder.calls == 0


def test_skip_for_small_talk_does_not_skip_a_real_content_question():
    embedder = _StubEmbedder()
    chunks = [_rc("a", 0.9)]
    result = retrieve(
        "What has he built with Redis?",
        k=5,
        embedder=embedder,
        store=_StubStore(chunks),
        skip_for_small_talk=True,
    )
    assert embedder.calls == 1
    assert result.chunks == chunks


def test_skip_for_small_talk_off_by_default():
    embedder = _StubEmbedder()
    retrieve("hi there", k=5, embedder=embedder, store=_StubStore([_rc("a", 0.9)]))
    assert embedder.calls == 1


# ---------- retrieval_params(config) ----------


def test_retrieval_params_returns_the_four_expected_keys(app):
    with app.app_context():
        params = retrieval_params(app.config)
    assert set(params) == {
        "min_score",
        "score_margin",
        "max_context_chars",
        "skip_for_small_talk",
    }


def test_retrieval_params_honours_config_overrides():
    cfg = {
        "ASSISTANT_RETRIEVAL_MIN_SCORE": "0.55",
        "ASSISTANT_RETRIEVAL_SCORE_MARGIN": "0.2",
        "ASSISTANT_MAX_CONTEXT_CHARS": "999",
        "ASSISTANT_SKIP_RETRIEVAL_FOR_SMALL_TALK": False,
    }
    params = retrieval_params(cfg)
    assert params == {
        "min_score": 0.55,
        "score_margin": 0.2,
        "max_context_chars": 999,
        "skip_for_small_talk": False,
    }


def test_retrieval_params_defaults_when_unset():
    params = retrieval_params({})
    assert params["min_score"] > 0
    assert params["score_margin"] > 0
    assert params["max_context_chars"] > 0
    assert params["skip_for_small_talk"] is True


# ---------- end-to-end budget acceptance ----------


def test_greeting_turn_with_no_public_tools_stays_under_2000_est_tokens():
    result = retrieve(
        "hi there",
        k=5,
        embedder=_StubEmbedder(),
        store=_StubStore([_rc("a", 0.9, content="x" * 500)]),
        skip_for_small_talk=True,
    )
    messages = build_messages(
        question="hi there",
        context_text=result.context_text,
        history=[],
        offered_domains=set(),
    )
    assert _est_tokens(messages) <= 2000


@pytest.fixture()
def small_corpus(app):
    with app.app_context():
        db.create_all()
        e = HashEmbedder(384)
        rows = {
            "projects/sre-infra": (
                "Redis queue worker cache-aside invalidation rate limiting -- "
                "the SRE Infra Layer project's own message queue and caching design."
            ),
            "bio": "Nelson Koskela, software engineer, JPMorgan, Houston, several side projects.",
            "faq": "Frequently asked questions about the site, the assistant, and how to reach Nelson.",
        }
        for source, text in rows.items():
            db.session.add(
                ContentChunk(
                    source=source,
                    kind="project" if source.startswith("projects/") else "bio",
                    title=source,
                    chunk_index=0,
                    content=text,
                    token_estimate=estimate_tokens(text),
                    embedding=e.embed_one(text),
                )
            )
        db.session.commit()
        yield


def test_content_question_end_to_end_stays_under_2500_est_tokens(app, small_corpus):
    with app.app_context():
        result = retrieve(
            "What has he built with Redis?",
            k=5,
            embedder=HashEmbedder(384),
            store=InMemoryStore(),
            max_context_chars=1800,  # comfortably fits this whole tiny test corpus
            skip_for_small_talk=True,
        )
        assert result.chunks  # grounding preserved -- something came back
        messages = build_messages(
            question="What has he built with Redis?",
            context_text=result.context_text,
            history=[],
            offered_domains=set(),
        )
        assert _est_tokens(messages) <= 2500

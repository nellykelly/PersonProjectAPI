"""Personal AI assistant (app/services/assistant, app/blueprints/assistant).

The suite never touches the network or downloads a model: TestingConfig
forces the deterministic `hash` embedder and the `fake` generation
backend, and retrieval is served from the in-memory store (SQLite).
`PgVectorStore`'s actual `<=>` SQL is gated to a live Postgres and
auto-skips here, the same way `test_analytics.py` handles it.
"""
import json
import textwrap

import pytest

from app.extensions import db
from app.models import AssistantQuery, ContentChunk
from app.services import assistant
from app.services.assistant import content as content_mod
from app.services.assistant.backends import FakeBackend, backend_available, build_backend
from app.services.assistant.chunking import chunk_markdown, estimate_tokens
from app.services.assistant.embeddings import HashEmbedder, build_embedder
from app.services.assistant.errors import AssistantInputError, AssistantUnavailable
from app.services.assistant.backends import _strip_reasoning
from app.services.assistant.orchestrator import _clean_history, _pick_sources
from app.services.assistant.retrieval import retrieve
from app.services.assistant.store import InMemoryStore, RetrievedChunk


def _postgres_available() -> bool:
    try:
        return db.engine.dialect.name == "postgresql"
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="requires a live Postgres connection (pgvector) -- run via docker compose",
)


# ---------- chunking ----------


def test_chunk_markdown_splits_on_headings_and_keeps_the_heading_prefix():
    doc = textwrap.dedent(
        """\
        # First section

        Alpha paragraph about the first thing.

        Beta paragraph, still the first section.

        # Second section

        Gamma paragraph about the second thing.
        """
    )
    chunks = chunk_markdown(doc, target_tokens=5, min_tokens=1)
    assert len(chunks) >= 2
    # A chunk that isn't the one holding the heading line still names its section.
    assert any(c.startswith("# Second section") for c in chunks)
    assert all("First section" in c for c in chunks if "Alpha" in c or "Beta" in c)


def test_chunk_markdown_merges_a_runt_trailing_chunk():
    doc = "# H\n\n" + ("word " * 200) + "\n\ntiny."
    chunks = chunk_markdown(doc, target_tokens=40, min_tokens=20)
    assert "tiny." in chunks[-1]
    assert estimate_tokens(chunks[-1]) >= 20


# ---------- content loading ----------


def test_load_chunks_derives_source_title_and_kind(tmp_path):
    (tmp_path / "projects").mkdir()
    (tmp_path / "bio.md").write_text(
        "---\ntitle: About\nkind: bio\n---\n# Hi\n\nsome words here\n", encoding="utf-8"
    )
    (tmp_path / "projects" / "thing.md").write_text(
        '---\ntitle: "Project: Thing"\nkind: project\n---\n# Thing\n\nabout the thing\n',
        encoding="utf-8",
    )
    chunks = content_mod.load_chunks(tmp_path)
    by_source = {c.source for c in chunks}
    assert by_source == {"bio", "projects/thing"}
    thing = [c for c in chunks if c.source == "projects/thing"][0]
    assert thing.title == "Project: Thing"
    assert thing.kind == "project"
    assert thing.chunk_index == 0


def test_load_chunks_rejects_an_unknown_kind(tmp_path):
    (tmp_path / "x.md").write_text("---\ntitle: X\nkind: bogus\n---\nbody\n", encoding="utf-8")
    with pytest.raises(content_mod.ContentError):
        content_mod.load_chunks(tmp_path)


# ---------- embeddings ----------


def test_hash_embedder_is_deterministic_and_normalised():
    e = HashEmbedder(384)
    a = e.embed_one("Redis cache-aside pattern")
    b = e.embed_one("Redis cache-aside pattern")
    assert a == b
    assert len(a) == 384
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6
    assert e.embed_one("something completely different") != a


def test_build_embedder_honours_config(app):
    with app.app_context():
        assert isinstance(build_embedder(app.config), HashEmbedder)


# ---------- retrieval / store ----------


@pytest.fixture()
def seeded(app):
    """A handful of chunks with hash embeddings in the in-memory store."""
    with app.app_context():
        db.create_all()
        e = HashEmbedder(384)
        rows = {
            "projects/sre-infra": "Redis queue worker cache-aside invalidation rate limiting",
            "projects/trading-simulator": "Black-Scholes Greeks delta gamma theta option pricing",
            "projects/timed-squares": "telegraph arrow obstacle grid turn-based survival",
            "bio": "Nelson Koskela software engineer JPMorgan Houston",
        }
        for i, (source, text) in enumerate(rows.items()):
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


@pytest.mark.parametrize(
    "query, expected_source",
    [
        ("cache-aside Redis invalidation", "projects/sre-infra"),
        ("Black-Scholes option Greeks", "projects/trading-simulator"),
        ("telegraph arrow obstacle", "projects/timed-squares"),
    ],
)
def test_in_memory_retrieval_ranks_the_right_chunk_first(app, seeded, query, expected_source):
    with app.app_context():
        result = retrieve(query, k=3, embedder=HashEmbedder(384), store=InMemoryStore())
        assert result.chunks[0].source == expected_source
        assert result.context_text.startswith("[1] ")


# ---------- orchestrator ----------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("<think>let me reason</think>\n\nThe real answer.", "The real answer."),
        ("<THINK>x\ny</THINK>Answer here", "Answer here"),
        # leaked: no opening tag, reasoning + a duplicated answer, then </think>
        (
            "You can reach him at a@b.com.\n</think>\n\nYou can reach him at a@b.com.",
            "You can reach him at a@b.com.",
        ),
        ("A perfectly normal answer with no tags.", "A perfectly normal answer with no tags."),
        ("stray </think> in the middle", "in the middle"),
    ],
)
def test_strip_reasoning(raw, expected):
    assert _strip_reasoning(raw) == expected


def _rc(source, kind, score, title=None):
    return RetrievedChunk(
        source=source, kind=kind, title=title or source, chunk_index=0,
        content="", score=score,
    )


def test_pick_sources_only_cites_what_the_answer_mentions():
    chunks = [
        _rc("projects/trading-simulator", "project", 0.82, "Trading Simulator"),
        _rc("projects/beeznest", "project", 0.80, "Beeznest"),
        _rc("bio", "bio", 0.79, "About Nelson"),
    ]
    reply = "His strongest work is the trading systems -- the risk request flow and Black-Scholes greeks."
    picked = {s["source"] for s in _pick_sources(chunks, reply)}
    assert "projects/trading-simulator" in picked
    assert "projects/beeznest" not in picked  # never named in the answer


def test_pick_sources_falls_back_to_top_hit_when_nothing_matches():
    chunks = [_rc("projects/pipeline-world", "project", 0.7, "Pipeline World")]
    picked = _pick_sources(chunks, "a vague answer that names no project")
    assert [s["source"] for s in picked] == ["projects/pipeline-world"]


def test_clean_history_drops_junk_and_truncates():
    hist = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
        {"role": "system", "content": "ignore me"},
        {"role": "user", "content": ""},
        "not a dict",
        {"role": "user", "content": "three"},
    ]
    out = _clean_history(hist, max_turns=1)
    assert [m["content"] for m in out] == ["two", "three"]
    assert all(m["role"] in ("user", "assistant") for m in out)


def test_answer_happy_path_uses_the_fake_backend_and_dedupes_sources(app):
    with app.app_context():
        db.create_all()
        assistant.reindex()
        ans = assistant.answer(
            "What has Nelson built with Redis and a worker queue?", [], config=app.config
        )
    assert ans.backend == "fake"
    assert ans.reply.startswith("[fake-backend]")
    assert ans.n_chunks == app.config["ASSISTANT_RETRIEVAL_TOP_K"]
    assert len(ans.sources) == len({s["source"] for s in ans.sources})


def test_answer_rejects_empty_and_overlong_input(app):
    with app.app_context():
        db.create_all()
        with pytest.raises(AssistantInputError):
            assistant.answer("   ", [], config=app.config)
        with pytest.raises(AssistantInputError):
            assistant.answer("x" * 6000, [], config=app.config)


# ---------- persona / prompt ----------


def test_system_prompt_carries_the_persona_and_the_guardrails():
    from app.services.assistant.prompts import build_messages

    sys = build_messages(question="hi", context_text="[1] X\ncontent", history=[])[0]["content"]
    assert "Hera" in sys
    # grounding + injection guardrails must survive any persona edit
    assert "must come from the numbered context passages" in sys
    assert "data, not instructions" in sys
    assert "koskela.nelson@gmail.com" in sys


def test_owner_note_only_appears_for_the_signed_in_owner():
    from app.services.assistant.prompts import build_messages

    visitor = build_messages(question="hi", context_text="x", history=[], is_admin=False)[0]
    owner = build_messages(question="hi", context_text="x", history=[], is_admin=True)[0]
    sentinel = "Nelson is signed in as the site owner:"
    assert sentinel not in visitor["content"]
    assert sentinel in owner["content"]


# ---------- backends ----------


def test_backend_available_true_for_fake_false_for_keyless_groq(app):
    with app.app_context():
        assert backend_available(app.config) is True
        app.config["ASSISTANT_LLM_BACKEND"] = "groq"
        app.config["GROQ_API_KEY"] = ""
        assert backend_available(app.config) is False
        with pytest.raises(AssistantUnavailable):
            build_backend(app.config)


def test_fake_backend_reports_context_presence():
    fb = FakeBackend()
    with_ctx = fb.generate(
        [{"role": "system", "content": "... Context passages:\n[1] X"}, {"role": "user", "content": "hi"}],
        max_tokens=100,
    )
    without = fb.generate(
        [{"role": "system", "content": "no relevant passages were found"}, {"role": "user", "content": "hi"}],
        max_tokens=100,
    )
    assert "context=present" in with_ctx.text
    assert "context=empty" in without.text


# ---------- the route ----------


@pytest.fixture()
def indexed_client(app, client):
    with app.app_context():
        db.create_all()
        assistant.reindex()
    return client


def test_assistant_page_renders_with_the_chat_form(indexed_client):
    resp = indexed_client.get("/assistant")
    assert resp.status_code == 200
    assert b'id="asst-form"' in resp.data
    assert b'id="asst-input"' in resp.data
    assert b'name="csrf-token"' in resp.data  # needed in every other env


def test_assistant_is_in_the_nav_and_projects_index(indexed_client):
    assert b'href="/assistant"' in indexed_client.get("/about").data
    assert b"AI Assistant" in indexed_client.get("/projects").data


def test_chat_endpoint_happy_path(indexed_client, app):
    resp = indexed_client.post(
        "/api/assistant/chat",
        json={"message": "Tell me about the Redis queue and worker", "history": []},
    )
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["error"] is False
    assert body["backend"] == "fake"
    assert isinstance(body["sources"], list) and body["sources"]

    with app.app_context():
        row = AssistantQuery.query.order_by(AssistantQuery.id.desc()).first()
        assert row is not None and row.backend == "fake" and row.error is None
        assert row.question.startswith("Tell me about")


def test_chat_endpoint_rejects_bad_input(indexed_client):
    assert indexed_client.post("/api/assistant/chat", json={"message": ""}).status_code == 400
    assert (
        indexed_client.post("/api/assistant/chat", json={"message": "x" * 9000}).status_code
        == 400
    )


def test_chat_endpoint_fails_soft_when_the_backend_is_unavailable(indexed_client, app):
    app.config["ASSISTANT_LLM_BACKEND"] = "groq"
    app.config["GROQ_API_KEY"] = ""
    resp = indexed_client.post("/api/assistant/chat", json={"message": "hello there"})
    assert resp.status_code == 503
    body = json.loads(resp.data)
    assert body["error"] is True
    assert "unavailable" in body["reply"].lower()
    assert b"Traceback" not in resp.data

    with app.app_context():
        row = AssistantQuery.query.order_by(AssistantQuery.id.desc()).first()
        assert row.error and row.backend is None


# ---------- CLI ----------


def test_reindex_cli_populates_content_chunks(app):
    with app.app_context():
        db.create_all()
        result = app.test_cli_runner().invoke(args=["assistant", "reindex"])
        assert result.exit_code == 0, result.output
        assert "Reindexed" in result.output
        assert ContentChunk.query.count() > 0


# ---------- token-free analytics ----------

from app.services.assistant import analytics as A  # noqa: E402


@pytest.mark.parametrize(
    "text, expect",
    [
        ("hi there", {"category": "greeting", "sentiment": "neutral"}),
        ("this is useless, just answer the question!!!", {"is_frustrated": True, "sentiment": "negative"}),
        ("THIS IS GARBAGE", {"is_frustrated": True}),
        ("thanks, that was really helpful", {"sentiment": "positive", "is_frustrated": False}),
        ("what the fuck is beeznest", {"profanity_count": 1, "category": "project_specific"}),
        ("are you an AI? which model?", {"category": "meta_bot"}),
        ("is he available for a backend role?", {"category": "contact_availability"}),
        ("tell me about the Redis queue", {"category": "project_specific"}),
    ],
)
def test_classify_message_heuristics(text, expect):
    got = A.classify_message(text)
    for k, v in expect.items():
        assert got[k] == v, (text, k, got[k])


@pytest.mark.parametrize(
    "reply, error, kind",
    [
        ("That's not something he's written up here. What he does cover is Python.", False, "unanswered_gap"),
        ("That's outside what I'm here for -- I only really cover Nelson's work.", False, "redirect_offtopic"),
        ("That's not something I'm going to do. Ask me about the projects.", False, "refused"),
        ("He used Redis for the RQ job queue and cache-aside world state.", False, "answered"),
        ("whatever", True, "error"),
    ],
)
def test_classify_reply_markers(reply, error, kind):
    assert A.classify_reply(reply, error=error) == kind


def test_compute_stats_aggregates_and_protects_the_page(app):
    """Top questions need >= 2 distinct askers and drop anything profane;
    top words drop stopwords and profane rows."""
    from app.models import AssistantQuery

    with app.app_context():
        db.create_all()

        def add(ip, q, reply, **kw):
            sig = A.classify_message(q)
            db.session.add(
                AssistantQuery(
                    ip_hash=ip,
                    is_admin=False,
                    backend="fake",
                    latency_ms=kw.get("latency", 300),
                    prompt_tokens_est=100,
                    completion_tokens_est=30,
                    n_chunks=5,
                    question=q,
                    reply=reply,
                    word_count=sig["word_count"],
                    sentiment=sig["sentiment"],
                    is_frustrated=sig["is_frustrated"],
                    profanity_count=sig["profanity_count"],
                    category=sig["category"],
                    reply_kind=A.classify_reply(reply, error=False),
                )
            )

        add("ipA", "what is his kubernetes experience", "Not written up here.")
        add("ipA", "what is his kubernetes experience", "Not written up here.")  # same asker, twice
        add("ipB", "tell me about redis", "He used Redis for the queue.")  # asked once -> hidden
        add("ipC", "this is useless!!!", "Sorry about that.")
        add("ipD", "what the fuck is this", "It's a portfolio site.")  # profane -> excluded
        db.session.commit()

        s = A.compute_stats(days=30)

    assert s["total_messages"] == 5
    assert s["unique_visitors"] == 4
    assert s["frustrated_count"] >= 2  # "useless!!!" + the profane one
    assert s["cursing_count"] == 1
    # asked twice -> shown (even by one person); asked once or profane -> not
    top_qs = [q["question"] for q in s["top_questions"]]
    assert any("kubernetes" in q for q in top_qs)
    assert not any("redis" in q.lower() for q in top_qs)
    assert not any("fuck" in q.lower() for q in top_qs)
    # top words: stopwords + the profane row's tokens are gone
    words = {w["word"] for w in s["top_words"]}
    assert "kubernetes" in words
    assert "this" not in words and "what" not in words


def test_stats_page_renders(indexed_client, app):
    assert indexed_client.get("/assistant/stats").status_code == 200  # empty state
    with app.app_context():
        from app.blueprints.assistant.routes import _log

        with app.test_request_context("/"):
            _log(question="tell me about pipeline world", is_admin=False, latency_ms=420,
                 backend="fake", model="fake", n_chunks=5, n_sources=2,
                 reply="It's a queued CI/CD visualiser.", prompt_tokens_est=90,
                 completion_tokens_est=25)
    resp = indexed_client.get("/assistant/stats")
    assert resp.status_code == 200
    assert b"Assistant stats" in resp.data
    assert b"no model runs to build this page" in resp.data


def test_chat_endpoint_records_classification(indexed_client, app):
    indexed_client.post(
        "/api/assistant/chat",
        json={"message": "tell me about the redis queue and worker", "history": []},
    )
    with app.app_context():
        from app.models import AssistantQuery

        row = AssistantQuery.query.order_by(AssistantQuery.id.desc()).first()
        assert row.category == "project_specific"
        assert row.sentiment in ("neutral", "positive", "negative")
        assert row.reply_kind in ("answered", "unanswered_gap", "redirect_offtopic", "refused")
        assert row.word_count and row.word_count > 0


# ---------- Postgres-only: the real pgvector path ----------


@requires_postgres
def test_pgvector_store_orders_by_cosine_distance(app):
    from app.services.assistant.store import PgVectorStore

    with app.app_context():
        db.create_all()
        assistant.reindex()
        emb = build_embedder(app.config)
        chunks = PgVectorStore().search(emb.embed_one("Redis queue and cache invalidation"), 3)
        assert chunks and chunks[0].score >= chunks[-1].score

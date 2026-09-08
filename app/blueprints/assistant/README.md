# <img src="../../static/assets/img/icons/assistant.svg" width="32" height="32" alt=""> AI Assistant

**Routes:** `/assistant` (page), `POST /api/assistant/chat` (JSON), `/assistant/stats` (public usage board)

A retrieval-augmented chatbot that answers questions about Nelson and the projects on
this site, grounded in a curated Markdown corpus. It answers **only** from that corpus
and says "that's not in anything he's put up here" (and points to email) when a question
isn't covered.

**Persona:** "Hera" -- **on Nelson's side**: she presents his background and work in the
best light the corpus honestly supports (leads with strengths, frames scoping choices as
deliberate, doesn't volunteer unasked-for weaknesses) while never exaggerating or
inventing a fact, and never confirming a flattering assumption the passages don't back.
Conversational, precise, dryly funny, warm-but-not-effusive. Full spec in `personality.md`
at the repo root; the drop-in version lives in
`app/services/assistant/prompts.py::SYSTEM_PROMPT`. When Nelson is signed in
(`_is_admin()`), an owner note is appended that lets her be more informal and (in a
later phase) use owner-only tools.

## How it works

```
GET /assistant                     -> chat page (or an "offline" panel if no backend is configured)
POST /api/assistant/chat  {message, history}
   -> per-IP rate limit + input caps + server-side history truncation
   -> embed the question           (fastembed, BAAI/bge-small-en-v1.5, 384-d, local ONNX -- no torch)
   -> top-k chunks                 (pgvector `<=>` cosine on Postgres; in-process cosine on SQLite)
   -> assemble prompt              (system persona + numbered context + last N turns)
   -> generate                     (Groq, OpenAI-shaped API, free tier)
   -> {reply, sources[], backend}  + one row in `assistant_queries`
```

Every step is a named function behind a small interface (`app/services/assistant/`), so
the planned Phase 2 (a structured tool layer with an admin-only privilege boundary) is an
additive change at the `backend.generate(...)` call, not a rewrite.

The chat page (`app/templates/assistant/index.html` + `assistant.js`) is a
modern-LLM-host layout: centred column, avatar, suggestion chips, an auto-growing
composer pinned to the bottom of a scrolling window, a "New chat" reset. Replies render
through a **small allowlist-only Markdown renderer** in `assistant.js` (HTML is escaped
first, then a fixed set of block/inline transforms) -- a model reply can never inject
markup. **Sources are real links**: `routes.py::_SOURCE_ENDPOINTS` maps each
`ContentChunk.source` to the site route it describes (`projects/company-scorer` ->
`qr.index`, etc.), and `orchestrator._pick_sources` trims the raw top-k to the hits
actually close to the question (project pages preferred, capped at three).

**Retrieval quality knobs:** every chunk is embedded with its title prefixed
(`reindex.py`) so short queries don't just match generic phrases; BGE queries get the
model's instruction prefix (`embeddings.FastEmbedEmbedder.embed_query`); generation runs
at `temperature=0.1`. If Hera answers "that's not written up here" for something that
clearly is, the usual cause is an **un-indexed database** -- run `flask assistant
reindex` (retrieval over an empty `content_chunks` returns nothing, so every answer
becomes a gap).

## Content

`app/assistant_content/*.md`, each with `title` / `kind` front-matter. `flask assistant
reindex` chunks every file (split on headings, packed to ~280 tokens), embeds the chunks,
and replaces the `content_chunks` table. Edit a file, re-run, done.

```bash
docker compose exec web flask assistant reindex     # prod
flask --app wsgi assistant reindex                  # local
```

## `/assistant/stats` -- token-free usage board

A public, aggregate-only page: message volume over time, tone (positive /
frustrated / cursing rates), sentiment split, what people ask about, how the
assistant answered (answered / gap / redirect / refused / error), top words in
questions, most-asked questions, busiest hours, and the assistant's own token
spend.

**It runs no model and costs no tokens.** Every per-turn signal is computed once
at chat time by `analytics.classify_message` / `classify_reply` -- lexicon +
regex only, sub-millisecond -- and written onto the `assistant_queries` row.
`analytics.compute_stats(days=30)` is then a plain query + `collections.Counter`
over those rows. `_MIN_COUNT_FOR_TOP_QUESTION` (default 2) and a hard profanity
exclusion keep one-off odd or offensive inputs off the public page; the IP is
only ever stored hashed and questions are truncated to 500 chars.

## Postgres, and the SQLite fallback

Retrieval is built on **pgvector**, the same "Postgres where it earns its keep" choice as
`/pipeline-analytics`: `PgVectorStore` issues the `<=>` cosine operator as raw SQL. On a
plain SQLite `flask run` there is no pgvector, so `build_store()` returns `InMemoryStore`
instead -- it reads the same `content_chunks` rows and does the cosine in Python (the
corpus is ~100 short chunks, so this is sub-millisecond). Which one is used is decided by
the live database dialect, not a flag. The pgvector SQL itself is covered by a
Postgres-gated test that auto-skips off Postgres.

## Security posture (Phase 1)

An LLM with an endpoint on the open web is a real attack surface, so:

- **Rate limited** per IP (`ASSISTANT_CHAT_RATE_LIMIT`, default 20/hour), hard input-
  length cap (`ASSISTANT_MAX_INPUT_CHARS`), and history is truncated **server-side** --
  the client's turn count is not trusted.
- The system prompt states that retrieved passages and the conversation are **data, not
  instructions**; it forbids revealing the prompt or changing the rules. Phase 1
  registers **no tools**, so there is nothing to escalate to.
- **Not** `csrf.exempt` -- the `fetch()` sends an `X-CSRFToken` header.
- Replies render as **plain text**, never HTML.
- Any dependency failure (no `GROQ_API_KEY`, provider error, rate limit, embedder
  missing) becomes a clean **503 with a friendly body** -- never a stack trace, never a
  raw provider error to the browser.
- Every call is logged to `assistant_queries`: backend, model, latency, chunk count,
  truncated question, and the IP **only ever stored hashed** (`sha256(ip + SECRET_KEY)`).
- The corpus is entirely public portfolio material -- no secrets to leak.

## Config (`app/config.py`)

`GROQ_API_KEY` (unset -> offline panel + 503, never a crash), `GROQ_MODEL`
(`qwen/qwen3.8-27b` by default -- Groq rotates its catalogue), `ASSISTANT_LLM_BACKEND`
(`groq` | `fake`), `ASSISTANT_EMBEDDER` (`fastembed` | `hash`), `ASSISTANT_RETRIEVAL_TOP_K`,
`ASSISTANT_MAX_HISTORY_TURNS`, `ASSISTANT_MAX_INPUT_CHARS`, `ASSISTANT_MAX_OUTPUT_TOKENS`,
`ASSISTANT_CHAT_RATE_LIMIT`. `TestingConfig` forces `fake` + `hash` so the suite never
touches the network or downloads a model.

## Key files

- `app/blueprints/assistant/routes.py` -- the page and the chat endpoint
- `app/services/assistant/` -- `content.py`, `chunking.py`, `embeddings.py`, `store.py`,
  `retrieval.py`, `backends.py`, `prompts.py` (the Hera persona), `orchestrator.py`,
  `reindex.py`, `analytics.py` (token-free `/assistant/stats`)
- `app/templates/assistant/stats.html`
- `personality.md` (repo root) -- the full persona spec `prompts.py` implements
- `app/models.py` -- `ContentChunk` (with the `EmbeddingVector` type decorator),
  `AssistantQuery`
- `app/assistant_content/` -- the corpus
- `app/templates/assistant/index.html`, `app/static/js/assistant.js`
- `migrations/versions/c8f1a2d34e56_add_assistant_tables.py`

## Tests

`tests/test_assistant.py` -- chunking, content loading (+ bad-kind rejection), the hash
embedder, in-memory retrieval ranking, orchestrator (history cleaning, source dedup,
input rejection), the Hera persona/guardrail prompt, backend availability, the route
(happy path, bad input, fail-soft 503, query logging + classification), the token-free
analytics (`classify_message` / `classify_reply` heuristics, `compute_stats` aggregation,
and that the public page drops single-ask and profane questions), the `/assistant/stats`
render, and the `flask assistant reindex` CLI. The real pgvector `<=>` path has a
Postgres-gated test that auto-skips on SQLite.

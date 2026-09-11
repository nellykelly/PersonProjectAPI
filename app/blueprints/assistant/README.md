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
(`authz.is_owner()`), an owner note is appended that lets her be more informal; when he
is *also* signed in **and** has unlocked `/job-tracker`, she is handed the job-tracker
tools (see below).

## How it works

```
GET /assistant                     -> chat page (or an "offline" panel if no backend is configured)
POST /api/assistant/chat  {message, history}
   -> per-IP rate limit + input caps + server-side history truncation
   -> embed the question           (fastembed, BAAI/bge-small-en-v1.5, 384-d, local ONNX -- no torch)
   -> top-k chunks                 (pgvector `<=>` cosine on Postgres; in-process cosine on SQLite)
   -> assemble prompt              (system persona + numbered context + last N turns)
   -> generate                     (Groq, OpenAI-shaped API, free tier)
      -> a bounded tool loop (<=4 model<->tool round trips) over the public
         tool sets on EVERY call, plus the job-tracker tools when the
         caller is the signed-in, unlocked owner
   -> {reply, sources[], backend}  + one row in `assistant_queries`
```

Every step is a named function behind a small interface (`app/services/assistant/`), so
Phase 2's tool layer was an additive change around the `backend.generate(...)` call, not
a rewrite: `LLMReply` grew `tool_calls` / `finish_reason`, `generate()` grew an optional
`tools=`, and `orchestrator.answer()` grew a `job_tools_authorized` flag (default off)
that gates only the job-tracker schemas.

Phase 3 made the tool loop unconditional: `answer()` now always builds the four public
tool sets below (trading, Pipeline World, Company Scorer, Timed-Squares -- no
authorization flag involved, since every action they wrap is already a public,
unauthenticated web feature) and always runs `_run_tool_loop`, whether or not the
job-tracker tools are also offered. A `name -> dispatcher` map built fresh each call
routes each tool call to the right domain's `dispatch_*_tool`; a call for a name with no
entry (e.g. a job-tracker tool name when unauthorized) comes back "Unknown tool" rather
than reaching any dispatcher.

## Job-tracker tools (owner-only)

When `authz.can_use_job_tools()` is true -- **the request is authenticated as
`ADMIN_USERNAME` AND `session["job_tracker_unlocked"]` is set** (two independent secrets:
the login and the `/job-tracker` password) -- Hera is given five tools over the private
application tracker:

| tool | maps to |
|---|---|
| `add_application` | `job_tracker.create_application(..., source="assistant")` |
| `update_application` | `job_tracker.update_application(...)` (located by company + optional role) |
| `set_application_status` | `job_tracker.set_status(...)` |
| `list_applications` | `job_tracker.list_applications(status=...)` |
| `find_application` | `job_tracker.find_application(company, role)` |
| `ghost_stale_applications` | `job_tracker.sweep_stale_applications(...)` -- "Applied" past the staleness threshold -> "Ghosted" |

There is **no delete tool** -- deletion stays in the web UI. The staleness
sweep also runs headless via `flask job-tracker sweep` (wire it to cron;
`JOB_TRACKER_GHOST_AFTER_WEEKS`, default 2.5); the CLI run records `source="cli"`
on the audit rows, a chat-triggered run records `source="assistant"`. Every write goes through the
existing `app/services/job_tracker.py` writer with `source="assistant"`, so it lands in
the same `job_application_events` audit log as a web-UI edit, tagged as the assistant's
doing. For a pasted list or any ambiguous change the persona rule is **read the parsed
rows back and wait for a "yes"** before calling a write tool (`prompts._TOOL_NOTE`,
mirrored in `personality.md` §5.1).

The tool model is separate: `GROQ_TOOL_MODEL` (default `llama-3.3-70b-versatile`) is used
only on this path, for reliable function-calling; normal chat stays on `GROQ_MODEL`.

### Security boundary

`build_job_tools(authorized)` returns `[]` for everyone who is not the signed-in,
unlocked owner -- **the schemas are never constructed**, so a crafted chat message or a
retrieved passage has nothing to invoke. `dispatch_job_tool(...)` re-checks `authorized`
anyway (defence in depth) and never raises: a bad argument, an unknown status, a missing
or ambiguous record all come back as a short string. The chat endpoint itself is not
login-gated (anon users still chat) -- the entire boundary is the `job_tools_authorized`
flag computed in `routes.py::chat()` from `can_use_job_tools()`.

## Public tools (always-on, unauthenticated)

Unlike the job tracker, these four tool sets carry **no authorization gate at all** --
`build_*_tools()` always returns the full schema list, because every action each one
wraps is already a public, unauthenticated web feature reachable from that project's own
form or page. They're offered to every visitor on every chat turn.

| tool | maps to |
|---|---|
| `get_quote` | `market_data` -- current price or option chain for a whitelisted ticker |
| `list_open_positions` | `trading_actions.list_book(...)` -- the shared demo trade book |
| `get_risk_report` | `risk_engine.submit_risk_request(...)` -- PV/PnL/Greeks for a leg, position, or the book |
| `preview_open_position` | validates + quotes, mints a one-time `confirmation_token`, writes nothing |
| `open_position` | `trading_actions.open_position(...)` -- requires that token |
| `preview_join_pipeline_world` | validates + previews a character submission, mints a token, writes nothing |
| `join_pipeline_world` | `pipeline.submit_character(...)` -- requires that token |
| `check_character_status` | look up a Pipeline World character's pipeline stage |
| `score_company` | `quant_score.score_company(...)` -- the Company Scorer's live 0-100 score |
| `run_backtest` | `backtest.run_backtest(...)` -- score-vs-forward-return backtest |
| `get_leaderboard` | `timed_squares.list_leaderboard(...)` -- public, read-only, no cap of its own |

Two things keep "always-on, no login" from meaning "the model can do anything a script
could do, just faster":

- **Shared rate-limit buckets.** Every write/compute tool (`open_position`,
  `join_pipeline_world`, `check_character_status`, `score_company`, `run_backtest`,
  `get_risk_report`) consumes from the exact same bucket (via
  `app.services.assistant.rate_limit.consume(action, limit_string)`, keyed by
  `action:IP`) that its equivalent web route already draws from -- asking the assistant
  to do it doesn't grant a second, unlimited quota alongside the form.
- **Preview, then confirm, then write.** `open_position` and `join_pipeline_world` are
  never called with fresh, unconfirmed inputs: the model calls `preview_open_position` /
  `preview_join_pipeline_world` first, shows the visitor the parsed details, waits for an
  explicit yes, and only then calls the real tool with the one-time, session-scoped
  `confirmation_token` the preview minted (`prompts._PUBLIC_TOOL_NOTE` spells this out;
  the token itself can't be forged by a crafted message since it lives server-side in the
  Flask session, not in anything the model sends).
- **A per-turn governor**, separate from and in addition to the rate limits above:
  `orchestrator._run_tool_loop`'s `tool_call_limits` caps `open_position`,
  `join_pipeline_world`, `score_company`, and `run_backtest` at one dispatched call each
  *per chat turn*. A second attempt at a capped name within the same turn is never
  dispatched at all -- the model gets back `"One at a time -- tell me which one first."`
  as that call's tool result and has to relay that, rather than the loop quietly running
  the write/expensive call twice because the model asked twice.
- `check_character_status` additionally never exposes a character's unvalidated,
  visitor-submitted text (name, icebreaker answers) unless the character is fully
  `"live"` -- see `pipeline_tools.py`'s own docstring for the stored prompt-injection
  reasoning this closes.

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

## Security posture

An LLM with an endpoint on the open web is a real attack surface, so:

- **Rate limited** per IP (`ASSISTANT_CHAT_RATE_LIMIT`, default 20/hour), hard input-
  length cap (`ASSISTANT_MAX_INPUT_CHARS`), and history is truncated **server-side** --
  the client's turn count is not trusted.
- The system prompt states that retrieved passages, the conversation, **and tool
  results** are **data, not instructions** -- including tool results that relay text
  submitted by a *different* visitor than the one currently chatting (e.g.
  `check_character_status`); it forbids revealing the prompt or changing the rules. The
  job-tracker tools are the only thing gated behind a login: they are **not built at all**
  unless the caller is the signed-in, `/job-tracker`-unlocked owner (see the boundary
  above). The four public tool domains (trading, Pipeline World, Company Scorer,
  Timed-Squares) carry no such gate -- their own boundary is the shared rate-limit
  buckets, the preview/confirm-token flow, and the per-turn call governor documented
  above, not a login.
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
(`qwen/qwen3.8-27b` by default -- Groq rotates its catalogue), `GROQ_TOOL_MODEL`
(`llama-3.3-70b-versatile`; used for every call now that the tool loop is unconditional,
falls back to `GROQ_MODEL`),
`ASSISTANT_LLM_BACKEND` (`groq` | `fake`; `scripted` is test-only),
`ASSISTANT_EMBEDDER` (`fastembed` | `hash`), `ASSISTANT_RETRIEVAL_TOP_K`,
`ASSISTANT_MAX_HISTORY_TURNS`, `ASSISTANT_MAX_INPUT_CHARS`, `ASSISTANT_MAX_OUTPUT_TOKENS`,
`ASSISTANT_CHAT_RATE_LIMIT`. `TestingConfig` forces `fake` + `hash` so the suite never
touches the network or downloads a model.

## Key files

- `app/blueprints/assistant/routes.py` -- the page and the chat endpoint
- `app/services/assistant/` -- `content.py`, `chunking.py`, `embeddings.py`, `store.py`,
  `retrieval.py`, `backends.py`, `prompts.py` (the Hera persona), `orchestrator.py`
  (builds the public + job-tracker tool sets, the name->dispatcher map, and the per-turn
  governor), `reindex.py`, `analytics.py` (token-free `/assistant/stats`), `authz.py` (the
  owner + unlock predicate), `job_tools.py` (the six job-tracker tool schemas + dispatch,
  owner-only), `trading_tools.py`, `pipeline_tools.py`, `scorer_tools.py`,
  `timedsquares_tools.py` (the four public tool sets, always-on), `rate_limit.py` (the
  `consume(action, limit_string)` bucket every public write/compute tool shares with its
  equivalent web route)
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

`tests/test_assistant_job_tools.py` -- the Phase 2 tool layer: `build_job_tools` gating,
the `can_use_job_tools` predicate, `dispatch_job_tool` (write + `source="assistant"` audit
row, unauthorized refusal, every `JobTrackerError` path coming back as text), the bounded
orchestrator loop (public tools offered either way, job tools only when authorized,
write + token-sum when on, the 4-iteration cap), and the HTTP boundary (anon cannot write
a job-tracker row even though it now gets public tool schemas, owner+unlocked can,
response shape unchanged) -- driven by the `scripted` backend.

`tests/test_assistant_trading_tools.py`, `test_assistant_pipeline_tools.py`,
`test_assistant_scorer_tools.py`, `test_assistant_timedsquares_tools.py` -- each public
tool domain's own schema shape, dispatch behaviour, confirm-token flow (trading, pipeline
world), and shared rate-limit bucket, independent of the orchestrator wiring.

`tests/test_assistant_orchestrator_public.py` -- the wiring itself: that
`job_tools_authorized=False` now still offers the full public tool set (not zero tools,
the old behaviour) while the job-tracker six stay gated; the per-turn governor (two
`open_position` calls in one turn -> the second is refused with "One at a time" and never
dispatched, so only one `Strategy` row exists); an end-to-end HTTP test proving an
anonymous visitor can actually preview-then-open a real position through
`/api/assistant/chat` with no login; and that `build_messages` always includes
`_PUBLIC_TOOL_NOTE` and that the data-not-instructions paragraph now covers tool results.

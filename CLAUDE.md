# CLAUDE.md

This file is new as of 2026-09-28 (a documentation-optimization pass) --
there was no prior CLAUDE.md in this repo. Keep it lean: always-loaded
context only. Anything long, subsystem-specific, or rarely needed belongs
in `docs/` or an `app/blueprints/*/README.md`, linked from the index below,
not inlined here.

## What this is

"Hera" -- Nelson Koskela's personal portfolio site (nelsonkoskela.dev): a
Flask app, one blueprint per section/project (trading sim, company scorer,
pipeline-world CI/CD sim + SRE infra layer, network-traffic analytics,
a Canvas game, a market-data warehouse dashboard, a RAG chatbot persona,
a private household suite, and a couple of personal tools), Docker +
Postgres + Redis for the full stack, SQLite/fakeredis fallback for
Docker-less local dev.

## Run it

```bash
# Docker (full stack: web, worker, postgres, redis)
cp .env.example .env   # fill in a real SECRET_KEY, etc.
docker compose up --build

# Local, no Docker (Windows: gunicorn doesn't run here, use this too)
python -m venv .venv && pip install -r requirements-dev.txt
cp .env.example .env
flask --app wsgi run
```

Without Docker, Postgres/Redis features fall back to SQLite/`fakeredis`
automatically -- everything works this way **except**
`/projects/pipeline-world/pipeline-analytics` (needs real Postgres for
`DATE_TRUNC`/window functions). Full detail: [`README.md`](README.md)
("Running it").

## Tests

```bash
pytest
```

Fully offline (external APIs monkeypatched, Redis/RQ run against
`fakeredis` in synchronous mode). A handful of Postgres-only tests
auto-skip here and only run against a real Postgres (e.g. via
`docker compose`).

## Gotchas -- read GOTCHAS.md in full, every session

This repo has seven failure modes that are silent, look like a different
bug, or only show up in production -- each one already happened once. Read
[`GOTCHAS.md`](GOTCHAS.md) before touching deploy, secrets, `.env`,
migrations, static assets, or `app/assistant_content/`. It's short; treat it
as part of this file, not an optional link.

## Other constraints

- **`app/assistant_content/*.md`** (bio, faq, product-status, resume,
  `projects/*.md`) are runtime data the app loads and serves as the
  assistant's knowledge base -- not developer documentation. Don't treat
  edits there like doc edits; they change what the live chatbot says.
- Don't modify application source code when the task is a documentation
  change -- keep `.md` edits and code edits in separate, reviewable
  changes.

## Conventions

- One Flask blueprint per site section (`app/blueprints/<name>/`), each
  with routes, templates, and usually its own `README.md` describing its
  data model, config, and tests -- read that blueprint's README before
  working in it, don't rediscover it from the code.
- Business logic lives in `app/services/`, not in route handlers.
- One test file per blueprint/service under `tests/`, run offline by
  default (see Tests above).
- `market-data-warehouse/` is a semi-independent sub-project (its own
  venv, its own dbt project, its own docs) -- see its own README before
  working there, don't assume root-repo conventions apply.

## Documentation index

Start here, then go only as deep as the task needs:

- **This file** -- always-loaded essentials.
- [`GOTCHAS.md`](GOTCHAS.md) -- always read in full, not just conditionally
  -- the seven silent failure modes above.
- [`README.md`](README.md) -- the full human-facing doc: every route,
  tech stack, project structure, hosting, deploy loop, cron jobs, TLS/DNS
  notes. The detail behind everything summarized above.
- [`TODO.md`](TODO.md) -- current outstanding work on this site
  specifically (career/cross-project TODOs live outside this repo).
- `app/blueprints/<name>/README.md` -- per-subsystem reference, read when
  working in that blueprint. Already conditionally-loaded by nature of
  living next to the code it documents; don't move these.
- `docs/SECURITY-NOTE.md` -- the OAuth-secret incident and what's still
  unrotated. Small; worth reading once.
- `docs/build-spec.md`, `docs/build-spec-tiny-jvm.md`,
  `docs/family-design.md` -- kept at these exact paths because live
  application code/templates cite them by path (see the historical-status
  banner in each). `build-spec.md` and (mostly) `family-design.md` describe
  already-built work; `build-spec-tiny-jvm.md` is still actively current
  (Tiny JVM is genuinely in progress -- see its Status header and `TODO.md`).
- `docs/archive/` -- completed build specs and retired session notes,
  kept for history, not current state. Read only if you need the *original
  reasoning* behind something already built; the corresponding blueprint
  README is the current source of truth.
- `docs/reference/` -- deep, rarely-needed material: `INTERVIEW-NOTES.md`
  (decisions/tradeoffs/bugs worth discussing in an interview) and the
  RAG/hashing brainstorm log it cites. Not linked from the live site.
- `legacy/README.md` -- retired pre-rebuild code, kept for history, not
  imported by the running app.
- `market-data-warehouse/README.md` -- entry point for that sub-project;
  it links its own `DIMENSIONAL_MODELING.md`, `HARDENING.md`, `PROMPT.md`,
  `RESUME_HANDOVER.md` in reading order.
- `tools/tinyjvm/HOW_TO_WRITE_A_PROGRAM.md` -- Tiny JVM's toy-language
  reference, read only when working on that project.

## Where new docs go

- A new silent/surprising failure mode discovered while working here: add it
  to `GOTCHAS.md`, not buried in a commit message or a blueprint README.
- A new per-subsystem technical doc: put it next to the code, as that
  blueprint's `README.md` (create one if it doesn't exist), not in
  `docs/`.
- A new cross-cutting design/decision doc that isn't tied to one
  blueprint: `docs/`, at the root (not a subfolder) if it's live/current
  and likely to be cited by path from code or another doc; `docs/archive/`
  once superseded or completed and no longer the current reference.
- A new rarely-needed deep-reference doc (interview prep, brainstorm logs,
  postmortems): `docs/reference/`.
- A new session-handover/scratch note: don't commit it to the repo root.
  If it needs to survive past the session, put it in `docs/archive/` from
  the start and say so in it, or better, fold its durable content into the
  relevant README/TODO and let the rest live in chat history.

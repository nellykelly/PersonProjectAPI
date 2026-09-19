# Session Handover — 2026-09-17

Written for whoever (human or the next Claude Code session) picks this
back up. This session's own work is below; the untouched 2026-09-11
handover follows further down and is still accurate (verified via
`git log` — nothing under `market-data-warehouse/` or the warehouse
blueprint has been committed since, so that section's "nothing
committed" claim still holds).

## What happened this session

1. **Relocated Claude Code's config home from C: to S:.**
   - Copied `C:\Users\nicep\.claude` (634.82 MB, 1,294 files) to
     `S:\claude-config` via robocopy. Copy verified clean (all files,
     0 mismatches/failures).
   - Set `CLAUDE_CONFIG_DIR` = `S:\claude-config` as a persistent
     Windows **User** environment variable
     (`[Environment]::SetEnvironmentVariable`).
   - **Not yet done: the old copy at `C:\Users\nicep\.claude` has NOT
     been deleted.** It's intentionally left in place as a fallback
     until a fresh session confirms it's actually reading from
     `S:\claude-config` (env var only applies to terminals/sessions
     started after it was set — this session was still running on the
     old path when the var was set).
   - **Next step for whoever picks this up:** start a brand-new
     terminal + Claude Code session, confirm it's reading memory/
     settings/history from `S:\claude-config` (e.g. this HANDOVER
     context, recent project memory, etc. should show up normally),
     then delete `C:\Users\nicep\.claude`.
   - Scope note: only `~/.claude` was relocated. The per-session
     scratchpad/temp dir (`C:\Users\nicep\AppData\Local\Temp\claude\...`)
     was deliberately left on C: — it's derived from the Windows-wide
     `TEMP`/`TMP` vars, and repointing those would affect every app on
     the machine, not just Claude Code. User chose to leave it.

2. **`/auto-mode-setup` — started, then put on hold.** It had already
   drafted an `autoMode.environment` block into the (old, C:) global
   `settings.json` before being paused. That draft carried over in the
   robocopy, so it's now sitting in `S:\claude-config\settings.json`.
   It has **not been reviewed or approved** — user asked to hold off
   until the drive relocation above was settled. Resume/review it next
   once the new session is confirmed working from S:.

---

# Session Handover — 2026-09-11 (still accurate, see note above)

Written for whoever (human or the next Claude Code session) picks this
back up. Nothing in this session was committed — everything below is
sitting in the working tree, 64 changed/new files across two areas.
`git status` / `git diff` is the ground truth; this is the map.

## Two things happened this session

1. **Tiny JVM** (`/projects/tiny-jvm`) — scaffolded, then **deferred**.
   No action needed; it's parked correctly. See below.
2. **Market Data Warehouse** — built essentially from scratch to a fully
   working, tested, live-verified state. This is where almost all the
   work is. See below.

Also: a personal writing-style skill (`/human`) was installed at
`C:\Users\nicep\.claude\skills\human\SKILL.md` (user-level, all
projects). It needs a fresh session to show up in the skill list —
that's the reason for this restart.

---

## Tiny JVM — deferred, don't touch unless resuming it

`/projects/tiny-jvm` is live on the site (scaffold + concept page only).
Implementation is blocked on missing toolchains (no Java/C compiler/
emscripten in this dev environment). Full status, the three considered
paths, and the decision are all in
`docs/build-spec-tiny-jvm.md` under **Status: DEFERRED**. Nothing to do
here right now.

---

## Market Data Warehouse — the real state

Two project roots:
- `market-data-warehouse/` — the data project (ingestion, dbt, tests).
  **Has its own venv**: `market-data-warehouse/.venv/`.
- Main Flask app (`app/`) — a dashboard at `/projects/market-warehouse`
  reading the warehouse. Uses the **root** venv (`.venv/` at repo root).

### Read these first, in this order

1. `market-data-warehouse/README.md` — how to run it, both targets.
2. `market-data-warehouse/DIMENSIONAL_MODELING.md` — **the important
   one**. Explains dimensional modeling + dbt from first principles, the
   full schema (ER diagram), why three facts share one grain, and the
   projection model's grain (security × future day × method × lookback).
3. `market-data-warehouse/HARDENING.md` — the adversarial pass: 10 real
   bugs found and fixed (H1-H10), what's deliberately deferred and why.
4. `market-data-warehouse/PROMPT.md` — the original brief this was built
   from.

### What's actually built

**Star schema** (`warehouse/models/marts/core/`): `dim_date`,
`dim_security`, `dim_exchange`, and a fact-table family all sharing the
security × trading-day grain: `fct_security_price`,
`fct_security_technical_daily` (SMA/Bollinger/RSI/52w-range),
`fct_security_risk_daily` (volatility/Sharpe/Sortino/beta/max-drawdown),
`fct_corporate_action`, plus `fct_security_price_projection` at a wider
grain (+ method + lookback_days — see DIMENSIONAL_MODELING.md §5).

**Three targets, all working:**
- **DuckDB** (local file) — the dev default, zero config.
- **MotherDuck** — **live, real account, real data**. Free "Lite" tier
  (permanent, not a trial). Token is in `market-data-warehouse/.env`
  (gitignored) and in the root `.env` (also gitignored) as
  `MOTHERDUCK_TOKEN` — already saved, nothing to redo. Account has a
  `market` database (the real one) alongside MotherDuck's own defaults
  (`my_db`, `sample_data` — ignore those).
- **Snowflake** — code-complete, `dbt parse --target snowflake` verified
  clean with a dummy token. Never run live (no free tier to test it on).

**Ticker universe** (`market-data-warehouse/ingest/tickers.txt`): AAPL,
MSFT, AMZN, GOOGL, NVDA, JPM, KO, XOM, plus **SPY** as the benchmark
(used for beta calculations).

**Verified numbers as of last build:** 94,346 RAW price rows, 12 dbt
models + 1 snapshot, **105/105 tests pass locally, 91/91 pass live on
MotherDuck** (both zero errors — the test-count difference between runs
is a dbt bookkeeping quirk across separate invocations, not missing
coverage; re-verify with a clean `rm -rf warehouse/target` build if it
matters).

**The Flask dashboard** (`app/blueprints/market_warehouse/`,
`app/services/market_warehouse.py`, `app/templates/market_warehouse/`,
`app/static/js/market_warehouse.js`):
- Stat tiles, latest-snapshot table, technical/risk indicator table.
- A relative-performance chart with a timeframe picker (presets + custom
  date range) backed by `GET /projects/market-warehouse/api/chart`.
- A price-projection chart: pick a **method** (linear trend / random
  walk drift / mean reversion) and a **lookback window** (30/60/90/180d)
  — both are filters over precomputed dbt rows, backed by
  `GET /projects/market-warehouse/api/projection`. **Not financial
  advice** disclaimers on the model docstring, the page banner, and the
  site-wide footer.
- Reads `MARKET_WAREHOUSE_DB_PATH` (app config / env var) — either a
  local file path (default) or `md:market` for MotherDuck. Currently the
  root `.env` has it set to `md:market`, so **the live site currently
  reads MotherDuck**, not the local DuckDB file. If that's not wanted,
  change/unset `MARKET_WAREHOUSE_DB_PATH` in the root `.env`.
- Never 500s: missing file / bad token / lock contention / unbuilt
  schema all render a friendly message instead.

### How to verify everything still works after restart

```powershell
# main app
.venv\Scripts\python.exe -m pytest -q
# expect: 649 passed, 2 skipped

# market-data-warehouse
cd market-data-warehouse
.venv\Scripts\python.exe -m pytest tests\ -q
# expect: 14 passed

# full dbt build (local DuckDB) -- IMPORTANT: always set DUCKDB_PATH for
# any *direct* dbt command, or it silently opens a different, empty
# database (H9 in HARDENING.md). run.py sets this correctly on its own.
$env:DUCKDB_PATH = (Resolve-Path .\warehouse\market.duckdb)
dbt build --project-dir warehouse --profiles-dir warehouse --target duckdb
# expect: 0 errors

# run the actual site
cd ..
.venv\Scripts\python.exe -m flask --app wsgi run
# then open http://127.0.0.1:5000/projects/market-warehouse
```

### Security note carried through the whole session

The MotherDuck token has never appeared in chat, in a command's visible
output, or in any file this session wrote content into by hand — it was
only ever sourced from the existing `.env` files at runtime. Keep doing
that: never ask for it in chat, never echo it in a command.

### Possible next steps (not started, just noted)

- Point the Flask dashboard's `MARKET_WAREHOUSE_DB_PATH` back to local
  DuckDB if MotherDuck shouldn't be the live source, or vice versa.
- Fundamentals (P/E, market cap) — the mechanism already exists
  (`dim_security` is SCD-1, `security_info_snapshot` gives history for
  free), just needs `INFO_FIELDS` extended in `ingest/pull_yfinance.py`
  — see HARDENING.md and DIMENSIONAL_MODELING.md §7.
- Earnings calendar as its own fact (sparse, event-grained) — same §7.
- A real live run against Snowflake if a trial is ever spun up.
- Resume Tiny JVM if/when a Java+C+emscripten toolchain becomes
  available in this environment.

---

## Nothing committed

`git status` shows 64 changed/new files, all uncommitted, spanning both
areas above. Review before committing — this file itself should
probably be deleted or moved out of the repo root once it's served its
purpose (it's a session artifact, not project documentation).

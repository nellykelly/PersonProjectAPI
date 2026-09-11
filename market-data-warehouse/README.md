# Market Data Warehouse

yfinance → `RAW` → **dbt** → a dimensional (star-schema) warehouse.
Snowflake is the intended target; a local **DuckDB** profile runs the
whole thing with no account so it can be developed and tested anywhere.

See [`PROMPT.md`](PROMPT.md) for the original brief,
[`DIMENSIONAL_MODELING.md`](DIMENSIONAL_MODELING.md) for exactly how dbt
and dimensional modeling fit together in this project (grain, conformed
dimensions, the fact-table family, SCD, the projection model's quirks),
and [`HARDENING.md`](HARDENING.md) for the adversarial pass that found
and fixed this project's real holes.

## Architecture

```
  yfinance endpoint
        │   ingest/pull_yfinance.py      (period="max": OHLC, adj close,
        │                                 volume, dividends, splits, metadata)
        ▼
  RAW schema  (RAW.price_history / dividends / splits / security_info)
        │   ingest/load.py  — idempotent per ticker, DuckDB or Snowflake
        ▼
  dbt staging  (views: typed, renamed, de-duped, partial bar dropped)
        │
        ▼
  dbt marts / core  — star schema
        dim_date ─────────┐
        dim_security ─────┼──▶ fct_security_price      (security × trading day)
        dim_exchange ─────┘        fct_corporate_action (security × action × date)
        + snapshots/security_info_snapshot  (metadata history for a future SCD-2)
```

### Star schema

| Model | Grain | Keys |
|-------|-------|------|
| `dim_date` | one calendar day | `date_key` (yyyymmdd int) |
| `dim_security` | one instrument (SCD-1) | `security_key` |
| `dim_exchange` | one exchange | `exchange_key` |
| `fct_security_price` | security × trading day | `security_key`, `date_key`, `exchange_key` |
| `fct_corporate_action` | security × action × date | `security_key`, `date_key`, `exchange_key` |

`fct_security_price` carries derived measures — `daily_return` (on the
adjusted close), `prior_close`, `change_abs`, `gap_pct`, `dollar_volume` —
so BI queries only join and filter.

## Run it (DuckDB)

```bash
cd market-data-warehouse
python -m venv .venv
.venv\Scripts\activate                 # PowerShell:  .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                   # defaults are fine for DuckDB

python run.py all --target duckdb
```

`run.py all` = `ingest` (yfinance → `RAW` in `warehouse/market.duckdb`)
then `build` (`dbt deps` → `seed` → `run` → `snapshot` → `test`).
Subcommands `ingest` / `build` run each half alone. Point at a different
universe with `--tickers path/to/list.txt`.

Inspect the result:

```bash
python -c "import duckdb; c=duckdb.connect('warehouse/market.duckdb'); \
print(c.sql('select * from marts.fct_security_price order by trade_date desc limit 5'))"
```

Example analytics queries are in [`analytics_queries.sql`](analytics_queries.sql).

> **Always go through `run.py`, or export `DUCKDB_PATH` yourself, before
> calling `dbt` directly.** The DuckDB profile's path default
> (`market.duckdb`) resolves relative to *whatever directory `dbt` is
> invoked from*, not `--project-dir`. `run.py` pins `DUCKDB_PATH` to one
> absolute file so ingestion and dbt always agree; a bare
> `dbt run --project-dir warehouse ...` from the repo root without that
> env var silently opens (or creates) a *different, empty* database and
> then fails confusingly ("schema does not exist") instead of loudly.
> Either run `python run.py build` / `all`, or `cd warehouse` first (dbt's
> own convention), or `export DUCKDB_PATH=$(pwd)/warehouse/market.duckdb`.

## Run it (MotherDuck)

**Live and verified**, not just structurally provided: `run.py all
--target motherduck` has actually run against a real MotherDuck account
— 94,346 price rows landed (9 tickers, including the `SPY` benchmark),
12 models + 1 snapshot built, **85/85 dbt tests pass**, re-ingesting is
idempotent (row counts held on a second run), and the resulting star
schema was queried back from MotherDuck's cloud, not a local file.

MotherDuck is DuckDB, hosted — a shareable web UI (closest thing to
Snowsight in this stack) over the free **Lite** tier: 10 GB storage,
10 Pulse compute-hours/month, no card, and it's a permanent plan, not a
trial. `DuckDBLoader` connects to it exactly like a local file; only the
path changes (`md:<database>` instead of a filesystem path), so the
ingestion and dbt code are identical to the local path above.

```bash
# 1. sign up at motherduck.com (free), then get a token:
#    https://app.motherduck.com/token
# 2. put it in .env — never in profiles.yml or a command line
echo "WAREHOUSE_TARGET=motherduck" >> .env
echo "MOTHERDUCK_TOKEN=<your token>" >> .env

python run.py all --target motherduck
```

Without `MOTHERDUCK_TOKEN` set, a bare `duckdb.connect("md:...")` falls
back to an interactive browser login — this project requires the token
explicitly instead, so a script fails fast with a clear message rather
than hanging. Validate the profile without spending any compute:

```bash
dbt parse --project-dir warehouse --profiles-dir warehouse --target motherduck
```

**A real gotcha, found on the first live run and now handled:**
`duckdb.connect("md:<name>")` does not create `<name>` — it only
*attaches* an existing database, and raises `no database/share named
'<name>' found` if it isn't there yet. `make_loader()` now bootstraps
through the account-level `md:` connection (no name = your default
catalog) and runs `CREATE DATABASE IF NOT EXISTS <name>` before the real
connection, every time — cheap and idempotent, so it's not worth gating
behind a first-run check.

## Run it (Snowflake)

Same dbt code — only the adapter and connection change.

```bash
pip install dbt-snowflake "snowflake-connector-python[pandas]"
# fill the SNOWFLAKE_* vars in .env
python run.py all --target snowflake
```

`ingest/load.py` uses `write_pandas` (explicitly targeting
`SNOWFLAKE_DATABASE`/`RAW_SCHEMA`, not relying on connection session
state) and a transactional delete-by-ticker before each load, so re-runs
replace rather than duplicate.

`SNOWFLAKE_DATABASE` is the one database everything lives in. Inside it,
dbt always builds into fixed schema names — `staging`, `marts`,
`snapshots` — regardless of target, via `macros/generate_schema_name.sql`
(kept deliberately unprefixed so DuckDB and Snowflake have an identical,
readable layout). **`SNOWFLAKE_DBT_SCHEMA` only sets dbt's own connection
default schema**, which nothing here actually uses since every model
declares its own `+schema`; it exists for parity with a
`snowflake_dbt` CLI default and isn't what places `fct_security_price`
into `marts` — that's the macro. `RAW_SCHEMA` (default `RAW`) is the one
that matters for where `ingest/load.py` lands raw tables and where the
dbt sources look for them.

Validate the profile without a live account:

```bash
dbt parse --project-dir warehouse --profiles-dir warehouse --target snowflake
```

`dbt parse` resolves the whole project and the Snowflake adapter with no
connection attempt; `dbt run`/`compile`/`debug` do connect and need real
credentials.

## Tests

- `pytest` (in this directory, 14 tests) — yfinance normalisation (network
  faked), retry/backoff behaviour, config reads the environment live
  rather than at import time, an all-null metadata row still lands
  string-typed, DuckDB loader idempotency **and** transactional rollback
  on a failed insert, the Snowflake loader's schema/database targeting
  and transactional delete against a fully faked `snowflake.connector`,
  and the MotherDuck target routing to `md:<database>` and failing fast
  without a token — none of the last two need a live account.
- `python run.py build` runs the dbt tests (64): `unique` / `not_null` on
  every key, `relationships` from both facts to all three dims,
  `accepted_values` on `action_type`, range checks on volume / dividend /
  ratio, a `ticker × date` uniqueness test on `stg_prices`, a direct
  `(security_key, date_key)` grain-uniqueness test on
  `fct_security_price`, two "no orphaned ticker" tests guarding the
  facts' inner join to `dim_security`, and a singular `high >= low` test.

## Scope

Price/market data only — fundamentals (income statement, balance sheet,
cash flow) are out of scope. `dim_security` is SCD-1; the snapshot exists
so an SCD-2 dimension can be added later without touching ingestion.

## Design decisions

- **DuckDB as a first-class dev target, not a mock.** The dbt code is
  identical for both; only the adapter and `profiles.yml` output change.
  This is what lets the project be built and tested with no Snowflake
  account. Both are pinned to the **dbt 1.9** series (stable; classic
  generic-test argument syntax).
- **Idempotency by delete-then-insert per ticker**, not `MERGE`. The
  natural keys are simple `(ticker, date)` style and a full re-pull of a
  ticker is cheap, so replacing its rows is simpler and just as correct
  as an upsert — and it works the same on DuckDB and Snowflake.
- **RAW is verbatim.** Typing, renaming, de-duplication and dropping the
  in-progress partial bar all happen in `staging`, so a bad transform is
  never destructive — re-run dbt, don't re-pull.
- **`date_key` as a `yyyymmdd` integer** computed with `EXTRACT`
  arithmetic in both the fact and `dim_date`, so the facts carry it
  without a join and the two definitions can't drift.
- **Portable day-of-week.** `dim_date` derives the ISO weekday from
  `dbt.datediff` against 1970-01-01 (a Thursday) with a sign-guarded
  modulo, because `EXTRACT(DOW …)` / `DAYOFWEEK` disagree between DuckDB
  and Snowflake and `%` keeps the dividend's sign for pre-1970 dates.
- **`dim_exchange` always has an `UNKNOWN` member**, and `dim_security`
  left-joins to it, so an unmapped `exchange` code never drops a security
  or a fact row.
- **Dividends and splits share `fct_corporate_action`** (one row per
  action, `action_type` discriminator) so "every cash and structural
  event for a name" is one filter.
- **Derived measures live on the fact** (`daily_return` off the adjusted
  close, `prior_close`, `change_abs`, `gap_pct`, `dollar_volume`) so BI
  queries are joins and filters only — see `analytics_queries.sql`.
- **Technical indicators, risk metrics, and the price projection are
  separate fact tables**, not more columns on `fct_security_price`, even
  where the grain is identical — a Kimball "fact table family." See
  [`DIMENSIONAL_MODELING.md`](DIMENSIONAL_MODELING.md) for the full
  reasoning, the grain of each, and why the projection needed
  `dim_date` to stop filtering out future dates.

## Verified

`python run.py all --target duckdb` against the default 9-ticker universe
(8 tracked securities + the `SPY` benchmark, full `period="max"`):
94,346 RAW price rows, 1,071 dividends, 43 splits, 9 metadata rows →
12 models + 1 snapshot built → **85/85 dbt tests pass**. Re-running
`ingest` leaves row counts unchanged (idempotent). `pytest` (14 tests,
`market-data-warehouse/tests/`) passes.

**MotherDuck, live** (not just parsed): the exact same universe was
ingested into a real free-tier MotherDuck account, built, and tested —
identical numbers, **85/85 dbt tests pass**, idempotency held on a second
live ingest, and the resulting `dim_security`/`fct_security_price` were
queried straight back from MotherDuck's cloud to confirm it. `dbt parse
--target snowflake` succeeds with a dummy token (no connection attempted,
Snowflake has no free tier to run this against live). See
[`HARDENING.md`](HARDENING.md) for the adversarial pass this all came
out of.

`dbt parse --target snowflake` succeeds with dummy credentials (no
connection attempted); `dbt compile`/`run` correctly attempt a real
connection and fail only on the dummy hostname — the expected boundary
without a real account. See [`HARDENING.md`](HARDENING.md) for the full
hole-list this pass found and fixed, and what's deliberately deferred.

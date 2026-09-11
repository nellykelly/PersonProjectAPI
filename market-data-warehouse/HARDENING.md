# Hardening pass

An adversarial re-read of the working project, done under a Nelson
mission after the initial build. Nine holes found (H1–H9): seven fixed
and verified without a live Snowflake account, one fixed and verified
live, and none silently dropped. Two items considered and explicitly
deferred, with reasons.

## Fixed

### H1 — facts silently drop orphaned tickers

`fct_security_price` and `fct_corporate_action` `inner join` to
`dim_security`. If a ticker's `security_info` pull ever failed while its
price/dividend/split pull succeeded, that ticker's rows would vanish from
the fact with no error — just a shorter table.

**Fix:** two loud singular tests instead of a silent left-join fallback —
`warehouse/tests/assert_no_orphaned_price_tickers.sql` and
`assert_no_orphaned_action_tickers.sql`. A test failure names the exact
orphaned ticker(s); nothing is auto-repaired underneath.

### H2 — Snowflake writes didn't target database+schema explicitly

`SnowflakeLoader` connected with `schema=cfg.schema`, but on a first run
that schema doesn't exist yet — `connect()` against a not-yet-created
schema can leave the session with no usable schema context. `write_pandas`
and the `DELETE` were then relying on that ambient session state instead
of saying where they meant to write.

**Fix:** `CREATE SCHEMA IF NOT EXISTS <db>.<schema>` followed by an
explicit `USE SCHEMA <db>.<schema>`; `write_pandas(..., database=,
schema=)` passed explicitly; the `DELETE` targets the fully-qualified
`<db>.<schema>.<table>`. Verified against a faked `snowflake.connector`
(`tests/test_ingest.py::test_snowflake_loader_first_run_creates_and_uses_schema_then_writes_qualified`)
— asserts the `USE SCHEMA` statement fires and `write_pandas` receives
the explicit database/schema/table_name, with no live account.

### H3 — an all-null metadata row could land as the wrong SQL type

`security_info` rows where every `yfinance.info` field came back `None`
(a thin or halted ticker) produced a pandas column with no type
information to anchor on. On DuckDB this surfaced concretely: `trim()` on
an all-null column DuckDB had inferred as `INTEGER` threw `Binder Error:
No function matches trim(INTEGER)` the first time this pass ran the
pipeline end to end. The Snowflake side was unverified but the same
inference risk applies to `write_pandas(auto_create_table=True)`.

**Fix:** `pull_security_info` now does `pd.DataFrame([row]).astype("string")`
before landing, so every metadata column is genuinely string-typed even
when entirely null. The staging model's defensive `cast(... as varchar)`
before every `trim()` stays as well — belt and braces. Verified with
`test_pull_security_info_all_null_is_still_string_typed`.

### H4 — delete + insert wasn't transactional

A crash between the `DELETE` and the `INSERT` in `DuckDBLoader.load()`
could leave a ticker's prior rows gone with nothing re-inserted —
correctness depended on the process never dying at exactly the wrong
moment.

**Fix:** `BEGIN TRANSACTION` / `COMMIT`, with `ROLLBACK` on any exception
before it propagates. Verified with
`test_duckdb_loader_rolls_back_on_insert_failure`, which forces the
`INSERT` to fail (a column-count mismatch against the already-created
table) and asserts the table still holds its pre-load row count rather
than being left empty.

**Residual risk, documented rather than claimed fixed:** the Snowflake
loader's `DELETE` is wrapped in `BEGIN`/`COMMIT` the same way, but
`write_pandas`'s `COPY INTO` is a separate operation from the DML
transaction and isn't verified to participate in it the same way — a
crash between the `DELETE COMMIT` and the `write_pandas` call could still
leave a ticker's Snowflake rows gone. This can't be verified without a
live account; noted here rather than silently assumed safe.

### H5 — no retry on yfinance calls

Untreated, this is the single most likely real-world failure: Yahoo
Finance throttles once a run touches more than a handful of tickers, and
every pull (`history`, `dividends`, `splits`, `get_info`, `fast_info`)
was one unretried network call.

**Fix:** `_with_retry()` — 3 attempts, exponential backoff
(1.5s → 3s), applied at every yfinance call site. A ticker that still
fails after 3 attempts is still logged and skipped for that feed only
(per-feed isolation was already correct — see "considered, not a bug"
below). Verified with `test_with_retry_succeeds_after_transient_failures`
and `test_with_retry_gives_up_after_configured_attempts`.

### H6 — `Settings()` read the environment at import time, not call time

`DuckDBConfig.path: str = os.environ.get(...)` and its siblings are class
*defaults*, evaluated once when `ingest.config` is first imported. In the
actual `run.py` CLI flow this happened to be harmless (`.env` loads and
`os.environ` is set before the first `import ingest.config`), but
`get_settings()` claiming to return live settings while actually
returning values frozen at import time is a latent bug for any other
caller — a test, a notebook, a long-lived process.

**Fix:** every field uses `field(default_factory=...)` reading
`os.environ` at construction time. Verified with
`test_settings_reflect_env_at_construction_not_import_time`, which
changes the environment *between* two `Settings()` constructions and
asserts both reflect their moment correctly.

### H8 — the Snowflake path had never been parsed or compiled

Written but never checked by any tool.

**Fix / verification:** installed `dbt-snowflake` + `snowflake-connector-python[pandas]`
and ran `dbt parse --project-dir warehouse --profiles-dir warehouse
--target snowflake` with dummy credentials — succeeds with no connection
attempt, same model/test/source/seed counts as the DuckDB target (9
models, 1 snapshot, 64 tests, 1 seed, 4 sources). `dbt compile` was also
tried: it *does* attempt a connection (unlike `parse`) and failed only on
`404 Not Found: post dummy-account.snowflakecomputing.com` — exactly the
expected boundary. `dbt run`/`test` against a live account need real
credentials and were not attempted (out of scope — see Deferred).

### H9 — a bare `dbt` command can silently open a different, empty database

Discovered while re-verifying H8: `dbt snapshot` run directly (not via
`run.py`) failed with `schema "staging" does not exist` even though the
same schema plainly existed and was queryable. Root cause: `profiles.yml`'s
DuckDB path default (`market.duckdb`) resolves **relative to the
directory `dbt` is invoked from**, not `--project-dir`. `run.py` pins
`DUCKDB_PATH` to one absolute path so ingestion and dbt always agree; a
bare `dbt ... --project-dir warehouse` from the repo root without that
env var creates a second, empty `market.duckdb` next to `run.py` instead
and then fails confusingly.

**Fix:** documented prominently in `README.md` (always use `run.py`, or
`cd warehouse` first, or export `DUCKDB_PATH` yourself) rather than
silently discovering it a second time. Not a code fix — the profile's
relative-path behavior is standard dbt-duckdb, and hard-coding an
absolute path in `profiles.yml` would break portability across machines.

## Documentation-only

### H7 — `SNOWFLAKE_DBT_SCHEMA` doesn't control where models land

The `.env.example` comment implied `SNOWFLAKE_DBT_SCHEMA` decides where
`staging`/`marts`/`snapshots` are built. In fact `macros/generate_schema_name.sql`
returns each model's own `+schema` verbatim, so every model lands in a
fixed schema name regardless of target — `SNOWFLAKE_DBT_SCHEMA` only sets
dbt's own *connection default* schema, which nothing here actually falls
back to. `RAW_SCHEMA` is the variable that matters (it's what
`ingest/load.py` writes to and what the dbt sources read from).

**Fix:** corrected in `README.md`'s Snowflake section. Left the macro
alone — making it schema-prefix on Snowflake only would reintroduce the
`<target>_<custom>` noise it was written to remove, and would make the
two backends' layouts diverge for no benefit.

## Considered, not a bug

- **Per-feed, per-ticker failure isolation.** `pull_all` already
  continues past one ticker/feed failure without aborting the whole run,
  and `Loader.load()`'s delete-list is built from the tickers *actually
  present* in the incoming frame, not the full requested batch — so a
  ticker that fails to pull for one feed doesn't have its existing RAW
  rows for that feed deleted. This was already correct; re-verified while
  looking for holes near it.

## Deferred (recorded, not silently dropped)

- **Incremental/watermarked ingestion.** Every run re-pulls full
  `period="max"` history for every ticker. At the project's deliberately
  small scale (8 tickers, `tickers.txt`) this is ~15s of network time and
  well within Yahoo's tolerance even with the new retry logic; adding
  watermark tracking (last-loaded date per ticker, incremental pulls)
  would be real engineering with no benefit at this scale. Revisit if the
  universe grows past roughly 50–100 tickers or ingestion runs more than
  a few times a day.
- **Incremental dbt materialization for `fct_security_price`.** Rebuilt
  as a full `table` every `dbt run` — at ~86k rows for 8 tickers this
  takes well under a second. Incremental materialization is warranted at
  millions of rows, not thousands; premature here.
- **Live Snowflake execution.** `dbt run`/`test` and the `SnowflakeLoader`
  against a real account need credentials this environment doesn't have.
  Everything that can be verified without them (H2, H3, H8) was; the code
  path is otherwise identical to the DuckDB path, which is fully
  exercised.

## Verification summary

- `pytest` (`market-data-warehouse/`): **12/12 pass**, including the six
  tests added for H2–H6 (five for the fixes above plus a rejection test
  for an unknown `WAREHOUSE_TARGET`).
- `python run.py all --target duckdb` (default 8-ticker universe): RAW
  86,872 rows landed (85,885 price / 936 dividends / 43 splits / 8
  metadata) → 9 models + 1 snapshot built → **64/64 dbt tests pass**
  (61 pre-existing + `assert_no_orphaned_price_tickers` +
  `assert_no_orphaned_action_tickers` + the `fct_security_price`
  `(security_key, date_key)` grain test).
- Re-running `ingest` leaves RAW row counts unchanged — idempotency
  holds after the transaction-wrapping change.
- `dbt parse --target snowflake`: green, no connection attempted.
  `dbt compile --target snowflake`: reaches the network layer and fails
  only on the dummy hostname, as expected without real credentials.

## Addendum — H10, found on the first live MotherDuck run

A third target, MotherDuck (DuckDB's free-tier hosted cloud), was added
after this hardening pass and reuses `DuckDBLoader` unchanged — the only
difference is the connection path (`md:<database>`). It was unit-tested
against a faked `duckdb.connect` the same way H2/H8 were, then actually
run live once a real account and token were available.

**H10 — `md:<name>` doesn't create the database.** The first live run
failed: `duckdb.connect("md:market")` raised `no database/share named
'market' found`. `md:<name>` only *attaches* an existing database; it
never creates one. This couldn't have been caught by the faked-connector
unit tests, which necessarily assume a `connect()` that succeeds — it
only surfaced by actually running against a live account, exactly the
category of bug H8/H9's connection-free validation is structurally
unable to catch.

**Fix:** `make_loader()` now bootstraps through the account-level `md:`
connection (no name = your default catalog) and runs `CREATE DATABASE IF
NOT EXISTS <name>` before the real connection. Idempotent, so it runs
unconditionally rather than behind a first-run check.

**Verified live**, not just against a fake: `run.py all --target
motherduck` against a real free-tier account — 85,885 RAW price rows,
9 models + 1 snapshot, **64/64 dbt tests pass**. Re-ran `ingest` a second
time: row counts held (idempotent). Queried `dim_security` and
`fct_security_price` back directly from MotherDuck's cloud afterward to
confirm the data actually landed there and not just that the commands
exited 0.

**Lesson for the pattern library:** a faked-connector unit test proves
the *code path* is right (right SQL, right target, fails fast on missing
config) but cannot prove the *external contract* is right (whether the
real service accepts what you're sending it). Both are needed; neither
substitutes for the other. H2 and H8's Snowflake work has the identical
residual gap — its faked-connector tests all assume `connect()`
succeeds, and it's never actually been tried against a live account.

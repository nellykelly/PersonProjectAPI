# ATL-101 practice sandbox

A hands-on practice environment for one realistic data-engineering ticket, end to end. It has look-alikes of Jira, Snowflake (Snowsight), the dbt Cloud IDE, Airflow, the S3 console and GitHub, plus a coach panel with hints and auto-graded acceptance criteria.

**Everything is fictional:** Atlas Conversion, Acme Insurance, the Pinnacle and Ironway legacy systems, and all the data.

**The ticket (ATL-101):** let the delta sync export run for a chosen list of policies, so QA can test specific policies without a full run.

## Run it locally
```
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     (macOS/Linux: .venv/bin/pip)
.venv/Scripts/python tools/server.py
```
Open http://127.0.0.1:8765. The first start builds a template sandbox with `dbt build` (~15 s); after that a new or reset sandbox is a copy of it (well under a second). Run on its own like this, you get one sandbox called `local`.

On the personal site it runs as the `sandbox` compose service behind the password gate at `/practice/`. See `app/blueprints/practice/README.md`.

## What's real and what's simulated
| App | Under the hood |
|---|---|
| Snowflake | A DuckDB file (`<session>/warehouse/atlas_dev.duckdb`) behind a Snowflake-flavoured layer: uppercase columns, `USE`/`SHOW`/`DESC`, Snowflake-style errors, TO_DATE/IFF/NVL/DATEADD/DATEDIFF |
| dbt | Real dbt-core with the dbt-duckdb adapter, running the project in `repo/` |
| Airflow | `build_dbt_commands` from the DAG file in `repo/dags` is run in a restricted subprocess (`tools/dag_runner.py`) and its dbt commands run. The scheduler and operators are stubs |
| S3 | The session's `s3/` folder |
| GitHub / CI | Branch, commit, PR and merge are simulated. CI runs the real acceptance tests (`tools/grader.py`) |

## Layout
- `baseline/`: the starting dbt project (the template's `repo/` starts as a copy)
- `answer_key/`: one correct solution
- `tools/`: `engine.py` (emulation), `server.py` (HTTP server + sessions), `grader.py` (CI tests), `raw_data.py` (fictional legacy data), `dag_runner.py` (restricted DAG evaluation), `sandbox_lockdown.py` + `profiles/` (the dbt profile actually used)
- `data/` (or `$SANDBOX_DATA`, `/data` in Docker; not committed): `_template/` and `sessions/<id>/{repo,warehouse,s3}`
- `Dockerfile`: the image the site's compose file builds
- `web/`: the single-page UI (plain JS, CodeMirror from cdnjs)

## Pipeline modelled
`RAW` (legacy as landed) → `STAGING` (base query per source → union → flatten into target tables) → `INTERMEDIATE` (record hash) → `PRE_EXPORT` (union shared tables across domains) → `EXPORT` (incremental, audit columns) → `EXPORT_VIEWS` (filtered by dbt vars) → S3.

## One sandbox per visitor
`server.py` reads the `X-Sandbox-Session` header (set by the site's proxy; no header means `local`) and points the engine at `data/sessions/<id>/` for that request through a context variable (`engine.use_box`, read everywhere as `engine.B()`). Each session has its own lock, so one visitor's 50 s grader run never blocks anyone else. New sessions copy `data/_template/` (built once at startup; `.ready` holds a fingerprint of `baseline/`, `raw_data.py` and the profile, so a deploy that changes those rebuilds it). Idle sessions are deleted after `SANDBOX_SESSION_TTL_HOURS` (default 72); past `SANDBOX_MAX_SESSIONS` (default 10) the least-recently-used idle one is evicted.

| Env var | Default | |
|---|---|---|
| `SANDBOX_DATA` | `./data` | template + sessions |
| `SANDBOX_SESSION_TTL_HOURS` | 72 | idle sessions deleted after this |
| `SANDBOX_MAX_SESSIONS` | 10 | |
| `SANDBOX_DBT_CONCURRENCY` | 2 | dbt processes at once, all sessions |
| `SANDBOX_GRADER_CONCURRENCY` | 1 | grader runs at once |
| `SANDBOX_RATE_PER_MINUTE` | 240 | API calls per session |
| `SANDBOX_SQL_TIMEOUT` / `SANDBOX_DBT_TIMEOUT` | 30 / 300 s | |

## Security model (it runs code visitors write)
dbt models, macros, `dbt show --inline`, worksheets and the DAG file are all visitor-written, so each path is closed off:
- **DuckDB, worksheets/previews/exports:** `engine.connect()` opens with `enable_external_access=false`, then `SET lock_configuration=true`. `read_csv('/etc/...')`, `COPY ... TO`, `ATTACH`, `INSTALL` and `SET` back all fail. Statements are interrupted after `SANDBOX_SQL_TIMEOUT`. The S3 export writes CSVs from Python rather than `COPY TO`.
- **DuckDB, inside dbt:** the profile dbt uses is `tools/profiles/profiles.yml` (not the editable `repo/profiles.yml`). Its `sandbox_lockdown` plugin runs on each connection: allow the session's own `seeds/` folder (dbt-duckdb loads seeds with `read_csv`), turn external access off, lock the config. This has to be a plugin: DuckDB only accepts `allowed_directories` as a runtime `SET` made *before* access is disabled, so neither the profile's `settings:` (re-`SET` on every cursor, which then errors) nor `config_options:` (connect-time) works.
- **dbt CLI:** only run/build/test/compile/seed/ls/list/show/parse. `--profiles-dir`, `--project-dir`, `--target`, `--state`, path flags and `..` are rejected. dbt runs with a scrubbed environment (`env_var()` sees nothing), a timeout, and a global concurrency cap.
- **Files:** writes are confined to the session's `repo/`, at most 200 KB each and 300 files, and only `.sql/.yml/.yaml/.csv/.md/.txt` plus the one DAG file. So no Python models (dbt-duckdb would execute them), no `profiles.yml`, nothing under `target/`/`logs/`, and `dbt_project.yml` `*-path(s)` must be plain folder names.
- **The DAG file:** never imported. `dag_runner.py` parses it, rejects anything outside an AST allow-list (imports beyond json/datetime/re/typing/airflow, dunder or frame attributes, `.format`, `open`/`eval`/`getattr`/...), executes it with a small builtins dict and stand-in modules, in a separate `python -I` process with a 10 s timeout, CPU/memory/file rlimits and an empty environment.
- **The container** (compose): internal-only network (no internet), no published port, no secrets in its environment, read-only root filesystem, all capabilities dropped, memory/CPU/pid limits, non-root user.

## Regression check
After any change, with an untouched repo the grader scores **2/7**, and with `answer_key/` copied over `repo/` it scores **7/7** plus both bonuses, each in its own session. Click Reset, then Coach > Check, or script it with `engine.use_box(...)` + `engine.check()`.

# Prompt: Market Data Warehouse (yfinance → dbt → Snowflake)

## Goal

Build a small analytics-engineering project that turns Yahoo Finance market
data into a queryable **dimensional warehouse** on Snowflake, modelled with
**dbt**.

## What to build

1. **Ingestion.** A Python job that, given a list of tickers, pulls *all
   available* trading data from the `yfinance` endpoint:
   - full daily price history (`period="max"`, unadjusted OHLC **and**
     adjusted close, volume),
   - the complete dividend history,
   - the complete stock-split history,
   - security metadata (name, exchange, currency, quote type, sector,
     industry, country).
   Land it verbatim in a `RAW` schema in the warehouse, one table per
   feed, with `ingested_at` and `source` audit columns. Re-runnable:
   a second run must not duplicate rows.

2. **Warehouse.** Snowflake is the target. All connection details come
   from environment variables / `profiles.yml` — nothing hard-coded. A
   local **DuckDB** profile must also work so the whole thing can be run
   and tested without a Snowflake account; the only difference is the dbt
   adapter.

3. **Transformations (dbt).**
   - `staging/` — one view per raw feed: typed, renamed to snake_case,
     de-duplicated (latest `ingested_at` wins), the in-progress partial
     bar dropped.
   - `marts/core/` — a **star schema**:
     - `dim_date` — one row per calendar day over the data's range.
     - `dim_security` — one row per instrument (current attributes;
       a dbt **snapshot** captures history so an SCD-2 version can be
       layered on later).
     - `dim_exchange` — conformed exchange dimension from a seed.
     - `fct_security_price` — grain: one security × one trading day.
       Measures: OHLC, adjusted close, volume, and derived daily return,
       prior close, gap %, dollar volume.
     - `fct_corporate_action` — grain: one security × one action
       (dividend or split) × effective date.
   - Surrogate keys on every dimension; foreign keys on every fact;
     `dim_date`/`dim_security`/`dim_exchange` join cleanly to both facts.

4. **Tests & docs.** dbt schema tests (unique / not_null on all keys,
   `relationships` from facts to dims, `accepted_values` on the action
   type), at least one singular test (`high >= low`), and model + column
   descriptions so `dbt docs` is meaningful.

5. **Runner.** One entrypoint (`run.py`) with `ingest`, `build`
   (`dbt seed`+`run`+`test`), and `all` subcommands, since `make` isn't
   available on the target machine.

## Constraints

- Do not touch the Flask app in this repo. This is a self-contained
  directory (`market-data-warehouse/`) with its own requirements and venv.
- Fundamentals (income statement, balance sheet, cash flow) are out of
  scope — price/market data only.
- Keep the default ticker universe small (a handful of large-caps) so a
  full run is quick; the universe is a config file, not code.
- Prove it end to end against **DuckDB** in this environment. The
  Snowflake path is the same dbt code with the adapter and `profiles.yml`
  swapped; provide that config but it is not expected to run here.

## Definition of done

- `python run.py all --target duckdb` ingests real yfinance data, builds
  every model, and passes every test.
- The star schema answers, in plain SQL, questions like "AAPL's
  dividend-adjusted annual return by year" and "20-day average dollar
  volume by security this month" with only joins on the surrogate keys.
- `README.md` explains the architecture, how to run it against DuckDB,
  and what changes for Snowflake.

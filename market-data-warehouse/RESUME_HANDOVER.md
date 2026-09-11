# Handover: Market Data Warehouse (dbt / dimensional modeling)

**Purpose of this file:** context for another AI assistant helping Nelson Koskela update his resume or prep for interviews. This project (or this part of his personal site) may not be in that assistant's context yet — this file is the primer. Source of truth for everything below lives in `market-data-warehouse/` in the `PersonProjectAPI` repo: `README.md`, `DIMENSIONAL_MODELING.md`, `HARDENING.md`, `PROMPT.md`.

## One-paragraph summary

Nelson built a full data warehouse from scratch: a Python ETL pipeline pulling real market data from Yahoo Finance, landing it in a raw layer, then a dbt project transforming it into a proper Kimball-style star schema (dimensions, fact tables, surrogate keys, SCD tracking, a five-table fact family sharing conformed dimensions). It runs identically against three engines — DuckDB locally, MotherDuck in the cloud (live-verified, not just built), and Snowflake (schema-validated, code-complete) — using the same dbt models with only the adapter swapped. A Flask dashboard reads the resulting warehouse read-only. The whole thing was then put through a deliberate adversarial "hardening" pass that found and fixed nine real correctness bugs before calling it done.

## Concrete numbers (resume-bullet material)

- **94,346** raw price rows, **1,071** dividend rows, **43** split rows, ingested for a 9-ticker universe (8 tracked securities + SPY as a benchmark), full history (`period="max"`).
- **12 dbt models + 1 snapshot**, built into a 3-dimension / 5-fact star schema.
- **85/85 dbt tests passing** — not just "tests exist": uniqueness/not-null/relationship tests on every key, range checks on financial measures, grain-uniqueness tests, orphan-row guards, a singular `high >= low` sanity test.
- **14 Python unit tests** for the ingestion layer (retry/backoff, idempotency, transactional rollback, schema targeting), independent of the dbt tests.
- **Three target engines, one codebase**: DuckDB (local dev), MotherDuck (cloud — actually run live against a real free-tier account, not just structurally supported: ingested, built, tested, and queried back from the cloud), Snowflake (dbt-validated with `dbt parse`, code-complete, deliberately not run live since there's no free tier to test against).
- **9 real bugs found and fixed** in a self-directed adversarial review pass after the initial build was "done" (see below) — a demonstrated debugging/code-review habit, not just a build habit.

## Technical skills this demonstrates

- **Dimensional modeling (Kimball method)**: fact vs. dimension design, grain as the central design discipline, surrogate keys via deterministic hashing, conformed dimensions shared across a fact-table family, SCD type 1 (current-state dimension) paired with a dbt snapshot doing SCD type 2 (full history) on the same source data.
- **dbt in production-shaped use**: staging → marts layering, `ref()`/`source()` DAG dependencies, generic and singular tests, seeds, snapshots, Jinja macros for cross-engine portability (e.g. a hand-rolled day-of-week calculation because DuckDB's and Snowflake's built-ins disagree), schema-name macros to keep multi-engine output layout identical.
- **Multi-warehouse portability**: the same dbt code targets DuckDB, MotherDuck, and Snowflake via profile/adapter swaps only — a real test of whether the modeling was done cleanly (engine-specific hacks would have broken this).
- **Python ETL engineering**: retry with exponential backoff on flaky external API calls, idempotent loads (delete-then-insert per ticker, verified with row-count-stable re-runs), transactional loads with rollback-on-failure, explicit schema/database targeting (not relying on ambient connection state), type-safety for all-null data rows.
- **Financial/quant domain modeling**: OHLCV price facts with derived measures (returns, gap %, dollar volume); a technical-indicators fact (SMA, RSI, 52-week range); a risk-metrics fact (volatility, Sharpe, Sortino, beta, max drawdown); a price-projection fact implementing three distinct forecasting methods (OLS trend regression, random-walk-drift baseline, and an AR(1)/Ornstein-Uhlenbeck mean-reversion estimate) with shared confidence-band treatment — genuinely modeled quant logic, not a toy example.
- **Adversarial self-review discipline**: after the initial build passed its own tests, Nelson ran a deliberate "find what's actually wrong" pass (documented in `HARDENING.md`) and found real issues that a "it works, tests pass" mindset had missed — silent orphan-row drops, a non-transactional load, ambient-state bugs in the Snowflake loader, no-retry API calls, a type-inference edge case. This is the kind of habit interviewers specifically probe for ("tell me about a bug that only showed up in production" / "how do you know your code is actually correct, not just passing its own tests").

## Architecture, briefly

```
yfinance API → Python ingest (retry, idempotent) → RAW schema
    → dbt staging (typed, de-duplicated views)
    → dbt marts: star schema
         dim_date, dim_security (SCD-1), dim_exchange   [conformed dimensions]
         fct_security_price                              [security × trading day — the base fact]
         fct_security_technical_daily                     ] same grain as price,
         fct_security_risk_daily                           ] split into separate
         fct_security_price_projection (security × future day × method × lookback)  ] fact tables
         fct_corporate_action (security × action × date)   [sparse, different grain]
    → Flask dashboard reads the marts layer read-only
```

The most interesting single design story, if asked to elaborate in an interview: the price-projection fact needed to write rows for *future* dates, but the date dimension originally stopped at "today" — a reasonable-looking limit that silently broke the feature (zero rows, no error) until traced back to the dimension, not the fact. The fix and the reasoning about why `method` and `lookback_window` had to become part of the grain (rather than a live-computed UI filter) are in `DIMENSIONAL_MODELING.md` §5 — good material for a "walk me through a non-obvious design decision" interview question.

## Where to look for more (for the other AI, if it wants to go deeper)

All paths relative to the `PersonProjectAPI` repo root:
- `market-data-warehouse/README.md` — how to run it, verified numbers, design-decisions list.
- `market-data-warehouse/DIMENSIONAL_MODELING.md` — the full technical narrative, written to teach dimensional modeling from first principles through this project's actual schema (ER diagram included).
- `market-data-warehouse/HARDENING.md` — all nine bugs found in the adversarial pass, what was fixed, what was deliberately deferred and why.
- `market-data-warehouse/warehouse/models/marts/core/` — the actual dbt SQL.
- `app/services/market_warehouse.py` — the Flask-side read layer, live at `/projects/market-warehouse` on the deployed site.

## Suggested resume bullet drafts (starting points, not final copy)

- Designed and built a Kimball-style dimensional data warehouse (dbt, 3 conformed dimensions, 5-table fact family, SCD-1/SCD-2) from a live external market-data API, portable across DuckDB, MotherDuck, and Snowflake with no engine-specific code paths.
- Implemented a financial risk/technical-indicator data pipeline (moving averages, RSI, Sharpe/Sortino/beta, three-method price projection) as tested dbt models, with 85 automated data-quality tests covering uniqueness, referential integrity, and business-rule ranges.
- Ran a self-directed adversarial hardening pass on a completed data pipeline, finding and fixing nine correctness bugs (transactional integrity, retry handling, type-safety, schema targeting) before they could surface in production.
- Verified a data warehouse design's portability by deploying the identical dbt codebase live against a cloud data warehouse (MotherDuck), with schema-only validation against Snowflake.

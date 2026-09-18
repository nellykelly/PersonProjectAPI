---
title: "Project: Market Data Warehouse"
kind: project
---

# What it is

A Kimball-style dimensional data warehouse built with dbt over real market data,
pulled through an idempotent ingestion job from the free `yfinance` feed. It runs the
identical dbt code, unchanged, against three different database engines --
DuckDB (a local file, the dev default), MotherDuck (a real, permanent free-tier
cloud account, not a trial), and Snowflake (code-complete, verified with `dbt parse`
against a dummy token, never run live since there's no free tier to test it on) --
with no engine-specific code paths anywhere in the models.

# The schema

A star schema under `warehouse/models/marts/core/`: three conformed dimensions
(`dim_date`, `dim_security`, `dim_exchange`) and a fact-table family that all share one
grain -- security x trading day: `fct_security_price`, `fct_security_technical_daily`
(SMA, Bollinger Bands, RSI, 52-week range), `fct_security_risk_daily` (volatility,
Sharpe ratio, Sortino ratio, beta, max drawdown), and `fct_corporate_action`, plus a
`fct_security_price_projection` table at a wider grain (security x future day x
projection method x lookback window). `dim_security` is SCD-1 with a companion
snapshot table giving history for free, so extending it with fundamentals data later
needs no new mechanism, just more source fields.

# The projection model -- not financial advice

A trend-projection chart offers three methods -- OLS linear trend, random-walk drift,
and AR(1) mean reversion -- each computable over several lookback windows, with the
"not financial advice" disclaimer on the model's own docstring, the page banner, and
the site-wide footer. It's a clearly labeled experiment in the shape of the data, not
a trading signal.

# Data quality

85 automated data-quality tests back the fact tables' quant and risk metrics. The
ticker universe is AAPL, MSFT, AMZN, GOOGL, NVDA, JPM, KO, XOM, plus SPY as the
benchmark used for beta calculations. A hardening pass found and fixed ten real bugs
along the way (documented as H1 through H10 in the project's own `HARDENING.md`),
including a Windows-specific finding about a build command silently opening the wrong,
empty database when a required environment variable wasn't set first.

# The live dashboard

`/projects/market-warehouse` reads the built warehouse live: stat tiles, a
latest-snapshot table, a technical/risk indicator table, a relative-performance chart
with a timeframe picker (presets plus a custom date range), and the price-projection
chart with its method and lookback-window pickers. The page never 500s -- a missing
file, a bad token, lock contention, or an unbuilt schema all render a friendly message
instead of an error. It also hosts a live demo of the site's own AI assistant: an
"autonomous stock analysis" panel that streams a real multi-tool LangGraph run over
Server-Sent Events (a quote, then a trend projection, then a written risk report for
one ticker), drawing its own separate Groq quota so traffic on the demo can never eat
into the main assistant chat's budget.

# What it demonstrates

Dimensional/Kimball modeling from first principles, a real dbt project (12+ models
plus a snapshot), portability across three database engines with zero engine-specific
branching, and a hardening discipline that treats "it built successfully" as different
from "it's correct" -- the kind of validation work this project's builder also did
professionally, reconciling risk-data systems record-for-record before every release.

---
title: "Project status: what's shipped vs. in progress"
kind: faq
---

# What "WIP" means on this site

A handful of project cards on `/projects` carry a small work-in-progress badge. That flag
is a per-project marker in the site's own project list, not a guess — it means the page
is genuinely live and worth visiting, but the write-up or the feature set underneath it
is still moving. Everything else on `/projects` is considered done: built, tested, and
not expected to change except for maintenance.

# Shipped, not WIP

- **Company Scorer** (`/projects/qr-quant-scraper`) — scores a company from SEC EDGAR
  filings and market data across four factor categories, then backtests the score
  against forward returns. Complete and stable.
- **Pipeline World** (`/projects/pipeline-world`) — the queued, seven-stage CI/CD
  visualizer with live Socket.IO updates and its own analytics page. Complete and
  stable.
- **SRE Infra Layer** (`/projects/sre-infra`) — the Redis queueing, caching, and
  rate-limiting layer underneath Pipeline World and the Trading Simulator's risk engine.
  Complete and stable.
- **Site Traffic Analytics** (`/projects/network-sniffer`) — the aggregate board over
  the app's own inbound and outbound traffic. Complete and stable.
- **Timed-Squares** (`/projects/timed-squares`) — the turn-based survival game with a
  public leaderboard. Complete and stable.
- **Market Data Warehouse** (`/projects/market-warehouse`) — the dbt dimensional
  warehouse (DuckDB / MotherDuck / Snowflake) with a live dashboard reading it. Built,
  tested, and verified working end to end; not flagged WIP.
- **Top Interview 150 Tracker** (`/projects/leetcode-150`) — a personal, login-gated
  interview-prep dashboard over LeetCode's official Top Interview 150 list. Complete and
  stable; deliberately small in scope (no backend state beyond per-account progress).

# In progress

- **Trading Simulator / PnL Tracker** (`/projects/trading-simulator`) — carries the WIP
  badge, but the core system is functionally complete: whitelisted-ticker booking,
  real Black-Scholes Greeks, a position/book-level risk engine running on a separate
  worker process, a live SSE watchlist, and an instrument catalogue with real OCC option
  symbols. What's actually still open is narrower than the badge might suggest — a
  multi-leg composer in the UI (the schema and risk engine already support multi-leg
  strategies; only the form to open more than one leg at once is missing) and support
  for bonds/bond forwards (blocked on there being no free bond-pricing data source
  comparable to `yfinance`). Anyone asking whether the simulator "works" should get a
  yes — the WIP badge tracks those two specific future-work items, not core
  functionality.
- **Tiny JVM** (`/projects/tiny-jvm`) — a compact stack-based VM written in C, compiled
  to WebAssembly and run live in the browser. The interactive demo is real: a WASM build
  of the C interpreter steps through one of three precompiled sample programs (a
  Fibonacci loop, a recursive factorial, and a simulated over-temperature GPIO alarm),
  each compiled ahead of time by an actual Java toolchain (lexer, parser, code
  generator). Not yet built: a Wokwi microcontroller firmware view, and free-form
  program editing (which needs a server-side compile endpoint that doesn't exist yet).
- **AI Assistant** (`/projects/assistant` — this project) — the chat assistant answering
  this question is itself mid-rebuild as of this writing. The retrieval layer (local
  embeddings, pgvector) and the always-on public tool set (trading, Pipeline World,
  Company Scorer, Timed-Squares) are shipped and working. In flight: replacing the
  hand-rolled tool loop with a LangGraph state graph, and adding an autonomous
  stock-predictor tool chain (quote lookup into a warehouse trend projection into a risk
  report) as a flagship example of the assistant deciding its own multi-step tool
  sequence. See the assistant's own write-up for the architecture, and treat any claim
  there about LangGraph or the stock-predictor as the intended, approved design —
  confirm against the live behavior if it matters, since both were still being built
  when this file was written.

# Earlier projects, not part of the live demos

- **Beeznest** — a B2B networking platform built with Ruby on Rails and SQLite, kept
  as an earlier project for range (a different framework and problem domain) rather
  than as a maintained demo. Placed 2nd at the StreetCode Accelerator Demo Day. Code is
  on GitHub, no live page on this site.

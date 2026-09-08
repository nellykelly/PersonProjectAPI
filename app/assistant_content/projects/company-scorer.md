---
title: "Project: Company Scorer"
kind: project
---

# What it is

A tool at /projects/qr-quant-scraper that scores a public company across four factor
categories and then backtests whether a higher score has historically lined up with
better forward stock performance. Scores are an educational demo built from public data;
they are not investment advice.

# Data sources

- **SEC EDGAR** — the official public XBRL "company facts" API (not HTML scraping),
  plus the published ticker-to-CIK mapping. It supplies revenue, net income, assets,
  liabilities, equity, current assets and liabilities, operating income, interest
  expense, and depreciation and amortisation.
- **yfinance** — current price, market cap, trailing P/E and P/B, and historical prices
  for the backtest's forward-return calculation.

XBRL tag names differ between filers (for example `Revenues` versus
`RevenueFromContractWithCustomerExcludingAssessedTax`), so each concept is resolved
through a small ordered list of aliases.

# Scoring

Four categories: Valuation (P/E, P/B, EV/EBITDA), Leverage and solvency
(debt-to-equity, current ratio, interest coverage), Growth (revenue and earnings growth
year over year), and Profitability (gross, operating and net margin, ROE, ROA).

Each raw metric is linearly normalised to a 0–100 sub-score against a fixed heuristic
range. Category scores are the average of their available metrics, and the overall score
is a weighted average across categories — equal weight by default, overridable by
environment variable, not hardcoded logic. If a metric or a whole category cannot be
computed for a company (a missing XBRL tag, an unusual taxonomy), it is dropped and the
remaining weights renormalise over what is actually available, rather than erroring or
treating the missing value as zero.

# Backtest

The backtest scores each ticker in a small basket using only fundamentals that were
**actually filed with the SEC on or before a date about a year ago** — filtered by
filing date, not period-end date, to avoid look-ahead bias — then compares that
historical score to the real price return since. It reports a per-ticker table plus a
Pearson correlation between score and forward return across the basket. This is a
demo-scale validation, not a rigorous research backtest.

# What it demonstrates

Working with a real government financial-data API and its messy taxonomy, a configurable
composite scoring model with graceful handling of missing data, and point-in-time
correctness in a backtest.

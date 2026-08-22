# QR -- Quant Company Scorer

**Routes:** `/projects/qr-quant-scraper`, `/projects/qr-quant-scraper/backtest`

Scores a company across four factor categories and backtests whether a higher score has
historically correlated with better forward stock performance.

> Scores are an educational demo built from public filings and market data.
> **This is not investment advice.**

## Data sources

- **SEC EDGAR** (`data.sec.gov`'s official public XBRL "company facts" API, plus the
  published ticker->CIK mapping) -- revenue, net income, assets, liabilities, equity,
  current assets/liabilities, operating income, interest expense, D&A. This is SEC's own
  API meant for programmatic use, not HTML scraping, and only requires an identifying
  `User-Agent` header (`SEC_EDGAR_USER_AGENT` in config).
- **yfinance** -- current price, market cap, trailing P/E and P/B, and historical prices
  (for the backtest's forward-return calc).

XBRL tag names vary between filers (e.g. `Revenues` vs.
`RevenueFromContractWithCustomerExcludingAssessedTax`), so each concept is resolved
through a small ordered alias list (`app/services/edgar.py: CONCEPT_ALIASES`).

## Scoring methodology

Four categories, exactly as specified:

- **Valuation** -- P/E, P/B, EV/EBITDA
- **Leverage/solvency** -- debt-to-equity, current ratio, interest coverage
- **Growth** -- revenue growth (YoY), earnings growth (YoY)
- **Profitability** -- gross/operating/net margin, ROE, ROA

Each raw metric is linearly normalized to a 0-100 sub-score against a fixed heuristic
range (`app/services/quant_score.py: METRIC_SPECS`), category scores are the average of
their available metrics, and the overall score is a **weighted** average across
categories (`config.py: QR_WEIGHTS`, equal-weight by default, overridable via env vars --
not hardcoded). If a metric or a whole category can't be computed for a company (a
missing XBRL tag, a filer with an unusual taxonomy), it's dropped and the surrounding
weights renormalize over what's actually available, rather than erroring or treating
missing data as zero.

EV/EBITDA approximates EV as `market cap + total liabilities` (no separate net-debt/cash
breakout is tracked) -- a documented v1 simplification.

## Backtest (the validation piece)

`/backtest` scores each ticker in a small basket (`config.py: QR_BACKTEST_TICKERS`) using
only fundamentals that were *actually filed* with the SEC on or before a date ~1 year ago
(filtered by filing date, not period-end -- avoiding look-ahead bias), then compares that
historical score to the real price return from then to now. Reports a per-ticker table
plus a Pearson correlation between score and forward return across the basket. This is a
demo-scale validation (a handful of tickers, one lookback window), not a rigorous
research backtest.

## Key files

- `app/blueprints/qr/routes.py` -- routes
- `app/services/edgar.py` -- SEC EDGAR client
- `app/services/quant_score.py` -- scoring engine
- `app/services/backtest.py` -- validation module
- `app/templates/qr/`

## Tests

`tests/test_qr.py` -- scoring math (normalization, category/overall weighting and
renormalization on missing data) plus route smoke tests with `edgar`/`market_data`
monkeypatched (no live network in CI).

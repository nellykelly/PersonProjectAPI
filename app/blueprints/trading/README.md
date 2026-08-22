# Trading Simulator / PnL Tracker

**Route:** `/projects/trading-simulator`

A booking-style simulator: open a simulated stock or option position on a whitelisted
ticker and watch its PnL update against real (delayed, free-tier) market data.

> **Simulation only.** No real money is involved. There's no login -- this is a shared,
> anonymous public trade book: anyone can open a position, and every position (across
> every visitor) is visible to everyone. Prices come from `yfinance` and are typically
> delayed ~15 minutes. Nothing here is financial advice.

## How it works

- **Data:** [`yfinance`](https://github.com/ranaroussi/yfinance) for underlying prices,
  historical price series, and the options chain (strikes/bid/ask/open
  interest/implied volatility). `yfinance` does **not** expose Greeks, so option
  positions are repriced locally with a Black-Scholes calculation
  (`app/services/pricing.py`) using the implied volatility captured at entry and the
  *current* underlying price -- entry IV is held constant, a documented simplification
  for a simulator without a live vol surface.
- **Abuse protection:** tickers are checked against a fixed whitelist
  (`config.py: TICKER_WHITELIST`, ~45 liquid large-caps/ETFs) before any `yfinance` call;
  `POST /open` is rate-limited per IP (`TRADING_RATE_LIMIT`, default 10/hour) and capped
  per anonymous session (`TRADING_MAX_OPEN_POSITIONS_PER_SESSION`, default 10).
- **Persistence:** SQLite via Flask-SQLAlchemy (`app/models.py: Position`), shared across
  all visitors -- no per-user data.
- **"Live" ticking:** the shared trade book auto-refreshes every 30s; a position's detail
  page polls a JSON quote endpoint every 15s and renders a PnL/price chart (Chart.js via
  CDN) built from `yfinance` history. Free-tier `yfinance` is rate-limited, so this is
  polling, not a true stream -- by design, per the build spec.
- **Failure handling:** every `yfinance` call goes through a retry-with-backoff and a
  short TTL cache (`PriceCache`); on persistent failure, routes show a clean
  "market data temporarily unavailable" state instead of a 500.

## Key files

- `app/blueprints/trading/routes.py` -- routes
- `app/services/market_data.py` -- yfinance wrapper (whitelist, cache, retries)
- `app/services/pricing.py` -- Black-Scholes + PnL math (pure functions, unit tested)
- `app/models.py` -- `Position`, `PriceCache`
- `app/templates/trading/`, `app/static/js/trading.js`

## Tests

`tests/test_trading.py` -- pricing math (Black-Scholes edge cases, PnL calc) plus route
smoke tests with `market_data` monkeypatched (no live network in CI).

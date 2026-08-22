# <img src="../../static/assets/img/icons/trading.svg" width="32" height="32" alt=""> Trading Simulator / PnL Tracker

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
- **Booking model:** modeled on how a real desk actually separates these concerns
  (see FpML's option/strategy trade representation) rather than one flat row per trade:
  - `Instrument` (`app/models.py`) -- reference/master data for one specific contract
    (underlying, strike, expiry, exercise style, contract multiplier). Deduped on
    identity (`app/services/instruments.py: get_or_create_instrument`) so every visitor
    trading the same AAPL $230 call shares one Instrument row instead of each copy
    re-embedding its own strike/expiry.
  - `Strategy` -- a book: a named container holding one or more `Leg`s. Today's UI only
    ever opens a single-leg strategy ("Single Leg"), but the schema doesn't assume that
    -- nothing structural stands in the way of a future multi-leg composer (straddles,
    iron condors) adding more legs to one open Strategy.
  - `Leg` -- one booked transaction (was the old flat `Position`): side (buy/sell),
    quantity, entry price/IV, references its Strategy and Instrument rather than
    embedding ticker/strike/expiry itself. `Leg.ticker`/`.kind`/`.strike`/`.expiry` are
    passthrough properties onto its Instrument, so most of the site's existing
    routes/templates read it exactly like the old `Position` did.
- **Risk (Greeks):** `pricing.py` computes real Black-Scholes delta/gamma/theta/vega/rho
  (`black_scholes_greeks` per-share, `position_greeks` scaled by quantity + contract
  multiplier), quoted the way traders actually read them -- theta per calendar day, vega
  and rho per 1-point move, not raw annualized/percent units. Shown on every open
  option's position-detail page. Verified against a standard textbook reference case
  (S=K=100, T=1yr, r=5%, vol=20%) in `tests/test_trading.py`.
- **Position &rarr; risk request &rarr; report/live feed** (`app/services/risk_engine.py`):
  rather than recomputing risk inline every page render, a caller explicitly *submits* a
  `RiskRequest` for one Leg (as-of-now, or under a what-if scenario -- spot/vol shock) and
  gets back a persisted `RiskResult`. That's what makes "every risk run against this
  position, and what each one found" a real, queryable fact (`GET /api/risk-requests/<id>`)
  instead of something reconstructed from a page load that's long gone. A live-feed SSE
  endpoint (`GET /api/positions/<id>/risk-feed`) resubmits a fresh request on an interval
  for as long as a viewer's connected -- each tick is a genuine new persisted row, not a
  throwaway client-side number. UI panel on the position-detail page; see
  `app/static/js/trading.js: initRiskPanel`.
  - `scenario_gamma` is a *different* computation from the closed-form `gamma` above --
    an empirical bump-and-revalue convexity (reprice at spot &plusmn;1%, measure how much
    the P&L itself curves), the actual meaning of "scenario gamma" on a risk desk. The two
    agree closely for vanilla Black-Scholes, which is itself a useful sanity check.
  - `ir_delta` is Rho, relabeled the way a real risk book would.
  - `ir_vega` (sensitivity to interest-rate *volatility*) needed a real, separate pricing
    model to be anything but a fabricated number: the app's everyday flat `RISK_FREE_RATE`
    has no volatility parameter at all. `pricing.py`'s Hull-White section
    (`black_scholes_price_stochastic_rates`, `ir_vega`) adds a small, explicitly-labeled
    one-factor stochastic short-rate extension (assumed, not calibrated, mean-reversion
    and rate-vol constants -- there's no cap/swaption vol surface in `yfinance` to
    calibrate against) used *only* to derive this one number via bump-and-revalue; every
    other Greek/PnL on the same row still comes from the ordinary flat-rate math.
- **"Live" ticking:** the shared trade book auto-refreshes every 30s; a position's detail
  page polls a JSON quote endpoint every 15s and renders a PnL/price chart (Chart.js via
  CDN) built from `yfinance` history. Free-tier `yfinance` is rate-limited, so this is
  polling, not a true stream -- by design, per the build spec.
- **Live watchlist grid** (`/watchlist`, `app/services/watchlist.py`): there's no free
  real-time market *stream* to plug into -- `yfinance` is pull-only, and genuine
  real-time streaming from an exchange is normally a paid-provider integration (Polygon,
  IEX, Alpaca, etc.) needing its own API key. Instead, a server-side background poller
  refreshes all whitelisted tickers on an interval and pushes each update to the browser
  instantly over **Server-Sent Events** -- same live-push pattern as the Network
  Sniffer -- so the grid feels live even though the underlying quotes are still
  yfinance's normal delayed data. Two things keep it from burning rate-limit budget for
  nothing: the poller only runs while at least one browser tab is actually connected
  (lazy start/stop), and only during NYSE market hours (a plain Mon-Fri 9:30-16:00
  America/New_York check -- no holiday calendar, a documented simplification).
- **Multi-stock live chart:** click any ticker tile on the watchlist page (up to 8) to
  add it as a plotted line -- each gets its own color and legend entry, and every SSE
  tick for a selected ticker appends a new point to its line (`spanGaps: true` so
  independently-timed updates across tickers still render as continuous lines rather
  than a broken zig-zag). Click again to remove it, or use "Clear selection."
- **Failure handling:** every `yfinance` call goes through a retry-with-backoff and a
  short TTL cache (`PriceCache`); on persistent failure, routes show a clean
  "market data temporarily unavailable" state instead of a 500.
- **SSE concurrency caps** (`app/services/sse_limits.py`): the watchlist stream and the
  risk-feed both hold a worker thread open indefinitely, and gunicorn only has a small,
  fixed pool of them -- without a cap, a handful of concurrent connections from one
  visitor could occupy every thread in every worker and hang the *entire site* for
  everyone else, not just degrade one feature (found in a security audit; the Network
  Sniffer's stream has the same fix, see that blueprint's README). Enforced via Redis
  (shared with Pipeline World's queue) so the cap holds across gunicorn's multiple worker
  *processes*, not just one -- a plain in-process semaphore wouldn't.

## Future work

- **Pluggable risk models per risk request.** Right now every `RiskRequest` runs the same
  fixed pricing model (flat-rate Black-Scholes for price/PnL/delta/gamma/theta/vega/rho,
  plus the Hull-White extension for `ir_vega`). A natural next step: let a risk request
  specify *which* model to price under (e.g. plain Black-Scholes vs. the Hull-White
  stochastic-rate variant vs. a future local-vol or Monte Carlo model), store which model
  produced each `RiskResult`, and let two requests against the same Leg under different
  models be compared side-by-side. That's realistic groundwork for a small standalone
  page/tool that runs the same position through several models and shows how much the
  risk numbers actually depend on model choice -- a genuinely useful thing to demo, and
  close to how a real quant desk actually reasons about model risk.

## Key files

- `app/blueprints/trading/routes.py` -- routes
- `app/services/market_data.py` -- yfinance wrapper (whitelist, cache, retries)
- `app/services/pricing.py` -- Black-Scholes price + PnL + Greeks math, plus the
  Hull-White stochastic-rate extension used only for `ir_vega` (pure functions, unit tested)
- `app/services/instruments.py` -- instrument reference-data lookup/dedup
- `app/services/risk_engine.py` -- RiskRequest/RiskResult: submit, run, persist
- `app/services/sse_limits.py` -- concurrency caps shared by every SSE endpoint
- `app/services/watchlist.py` -- market-hours check, pub/sub, background poller
- `app/models.py` -- `Instrument`, `Strategy`, `Leg`, `RiskRequest`, `RiskResult`, `PriceCache`
- `app/templates/trading/`, `app/static/js/trading.js`, `app/static/js/watchlist.js`

## Tests

- `tests/test_trading.py` -- pricing math (Black-Scholes edge cases, PnL calc) plus route
  smoke tests with `market_data` monkeypatched (no live network in CI).
- `tests/test_watchlist.py` -- market-hours boundary cases, the pub/sub layer, direction
  tracking across successive polls, and that a poll sweep stops early once the last
  subscriber disconnects. (The SSE route itself isn't hit through the HTTP test client,
  same reasoning as the Network Sniffer's -- it's an intentionally infinite generator.)

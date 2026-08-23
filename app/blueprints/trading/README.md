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
  `RiskRequest` -- against one leg, a whole position, or the whole book, see below -- and
  gets back a persisted result. That's what makes "every risk run, against what, and what
  each one found" a real, queryable fact instead of something reconstructed from a page
  load that's long gone.
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

- **Pluggable risk models** (`app/services/risk_models/`): a `RiskRequest` names *which*
  model priced it (`model_key`), not just what it found. Two are registered today --
  **Trader Granular** (fast closed-form Black-Scholes Greeks, the model behind the live
  feed) and **Full Revalue** (reprices across a &plusmn;20% spot ladder and reports
  measured, not formula-derived, convexity -- slower, but answers what closed-form Greeks
  structurally can't: what a large move actually does). Both implement the same
  `RiskModel` interface (`key`/`name`/`summary`/`method`/`good_for`/`limitations` metadata
  plus `run(ctx) -> ModelRun`), so the report page can show which model answered, and two
  requests against the same position under different models are directly comparable
  instead of silently assumed to agree.

- **Position- and book-level risk** (`submit_risk_request(*, leg_id=…)` /
  `(*, strategy_id=…)` / `(*, book=True)`): every leg priced by one request shares a
  **single market snapshot**, fetched once per distinct ticker before any leg is priced.
  This is the entire reason position-level pricing exists: pricing legs one at a time as
  they're reached would mark a multi-leg spread against several different instants, so
  the net Greeks would describe a position that never existed at any single moment.
  `book=True` prices every open leg across every position in the whole shared trade book
  in one request -- same guarantee, wider scope. A position is its own queryable entity
  (`/strategies/<id>`, `/api/strategies/<id>`): its legs, its risk history, a panel to run
  risk against the whole thing. The all-positions page (`/strategies`) can run risk
  against the entire book at once from a single button.
  - **A leg is not the same thing as a single trade.** A leg is one component of a
    genuinely multi-part strategy (a straddle's two legs, a swap's fixed/floating legs) --
    a standalone stock or option trade with no siblings isn't a leg of anything, even
    though the schema still stores it as a one-row `Strategy`. The UI reflects this: a
    single-instrument run says "Single trade" and skips the "Legs priced"/"Per-leg
    breakdown" sections entirely (they'd just repeat the one result); those only appear
    once a request actually spans more than one leg. A book-level run goes a step further
    and says "instrument," not "leg," since its rows span *unrelated* positions, not
    parts of one strategy.

- **Instrument codes** (`app/services/instruments.py: occ_code`): every `Instrument` gets
  a real, industry-standard **OCC option symbol** -- root ticker + 6-digit expiry (YYMMDD)
  + C/P + 8-digit strike (strike &times; 1000) -- e.g. `AAPL270115C00300000`, or just the
  ticker for stock. Deterministic from the instrument's own identity fields, so it's
  always unique and doubles as a lookup key without a separate counter table. The
  instrument catalog (`/instruments`, searchable by code or ticker; `/instruments/<code>`
  for one contract's reference data plus every leg across every position that has ever
  traded it) is what "one master row per contract" is actually *for* -- a way to query it,
  not just a normalization detail in the schema.

- **Risk pricing runs on a separate worker, not inline in the web request**
  (`run_risk_request_job` in `risk_engine.py`, consumed by `worker.py`): `submit_risk_request`
  creates the `RiskRequest` row, enqueues a job onto a dedicated `risk_engine` Redis queue
  (see [SRE Infra Layer](../sre_infra/README.md) -- same worker pool Pipeline World's join
  pipeline already used, just a second kind of job), and blocks until the row's own
  `status` stops reading `pending`. The actual Black-Scholes/market-fetch work happens in
  the `worker` container, a genuinely separate process -- verified live by watching
  `docker compose logs worker` show `run_risk_request_job(...)` execute there while the
  web response still comes back synchronously with a finished report. Because a worker
  runs in a different process, it can't raise a Python exception back across that
  boundary: on failure it stores a plain-text reason on `RiskRequest.error` instead
  (`"market_data: ..."` for a market-data outage), and `submit_risk_request` re-raises the
  right exception type from that string after the wait completes, so every existing
  caller/route keeps working unchanged. Under `TESTING`, RQ runs the job inline inside
  `enqueue()` (no real worker needed), so the row is already finished before the first
  poll -- same code path as production, just synchronous.
- **Book overview** (on the section's own index page, above the open-positions table): a stat-grid
  (net PV/PnL/Delta/Gamma/Theta/Vega) plus a line chart of PnL over time, both pulled from
  the **last completed book-level `RiskRequest`**, not recomputed on page load -- the same
  "a risk request is a persisted fact" rule the rest of this feature follows. The chart
  plots one point per past book-level request (date on the x-axis), not a per-instrument
  breakdown of a single snapshot -- "how has the book been trending" is the more useful
  question once there's more than one data point, and it's a real history already sitting
  in the database rather than something that needs computing fresh. Dollar figures on the
  overview tiles go through a `money` Jinja filter (`app/template_filters.py`) that
  abbreviates past `$100K` (`K`) and `$1M` (`M`) rather than printing the full
  comma-grouped figure -- a 6-figure value, especially a negative one with its own minus
  sign, is wide enough to overflow a stat-tile's fixed width otherwise (found live, twice,
  in two different shapes -- see `docs/INTERVIEW-NOTES.md`).
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

- **Bonds and bond forwards.** The position model (`Strategy` holding one or more `Leg`s,
  each pointing at an `Instrument`) doesn't assume options/stock -- adding a new
  `instrument_type` and a matching pricing/risk-model path is additive, not a schema
  change. Not built yet because there's no free bond-pricing data source equivalent to
  `yfinance` wired up.
- **A multi-leg composer in the UI.** The schema and the risk engine have supported real
  multi-leg strategies (a spread, a straddle) since this session's position-level rework
  -- what's missing is a form that opens more than one leg into the same `Strategy` at
  once. Today every UI-opened position is a one-leg `Strategy`; a multi-leg spread only
  exists in the data model if built directly (as the test suite does).

## Key files

- `app/blueprints/trading/routes.py` -- routes, including `/strategies`, `/strategies/<id>`,
  `/instruments`, `/instruments/<code>`, and the book-level `/risk-requests`
- `app/services/market_data.py` -- yfinance wrapper (whitelist, cache, retries)
- `app/services/pricing.py` -- Black-Scholes price + PnL + Greeks math, plus the
  Hull-White stochastic-rate extension used only for `ir_vega` (pure functions, unit tested)
- `app/services/instruments.py` -- instrument reference-data lookup/dedup, OCC code
- `app/services/risk_models/` -- the model registry (`base.py`, `trader_granular.py`,
  `full_revalue.py`, `__init__.py`)
- `app/services/risk_engine.py` -- RiskRequest/RiskResult: submit (enqueue + wait),
  `run_risk_request_job` (the actual pricing, run by the worker)
- `app/services/queue.py` -- the shared RQ/Redis queues (`pipeline_world`, `risk_engine`),
  consumed by `worker.py`
- `app/services/sse_limits.py` -- concurrency caps shared by every SSE endpoint
- `app/services/watchlist.py` -- market-hours check, pub/sub, background poller
- `app/models.py` -- `Instrument`, `Strategy`, `Leg`, `RiskRequest`, `RiskResult`, `PriceCache`
- `app/templates/trading/`, `app/static/js/trading.js`, `app/static/js/watchlist.js`

## Tests

- `tests/test_trading.py` -- pricing math (Black-Scholes edge cases, PnL calc) plus route
  smoke tests with `market_data` monkeypatched (no live network in CI).
- `tests/test_risk_models.py` -- the model registry, position- and book-level pricing (the
  single-shared-snapshot guarantee is asserted directly, with a mock price that changes
  on every call), worker-job error translation, instrument codes/lookup, and the
  leg-vs-single-trade wording split.
- `tests/test_watchlist.py` -- market-hours boundary cases, the pub/sub layer, direction
  tracking across successive polls, and that a poll sweep stops early once the last
  subscriber disconnects. (The SSE route itself isn't hit through the HTTP test client --
  it's an intentionally infinite generator, which would hang the test runner trying to
  fully consume it; the risk-feed SSE route has the same reasoning.)

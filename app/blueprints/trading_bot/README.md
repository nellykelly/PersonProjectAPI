# <img src="../../static/assets/img/icons/trading-bot.svg" width="32" height="32" alt=""> Trading Bot

**Routes:** `/projects/trading-bot`, `/projects/trading-bot/research`

A live dashboard over a real, separate project (`S:\Tradingbot`, not part of this repo): a
systematic trend-following stock strategy trading a real Alpaca brokerage account, next to the
walk-forward-validated research behind it. See `WEBSITE_HANDOVER.md` in that repo for the original
build spec this blueprint was implemented from.

> **Not a simulator.** Unlike the Trading Simulator (simulated positions against delayed
> `yfinance` data), this page reflects a real account -- paper money today, possibly small real
> money later. Still not investment advice; see the disclaimer on both pages here.

## Password-gated -- the most sensitive data on this site

A real brokerage account is more sensitive than anything else this site exposes, so this
blueprint is gated as hard as `/documentation` and `/job-tracker`, and the same way: a signed
session flag set by a correct password POST (`app/blueprints/trading_bot/routes.py`), nothing
rendered to an unauthenticated request -- not even the "is it paper or live" banner -- and it
**fails closed**. If `TRADING_BOT_PASSWORD_HASH` isn't configured, there is no password that opens
the section; it 503s instead of defaulting open.

On top of the gate, it's deliberately hidden: its own password (`TRADING_BOT_PASSWORD_HASH`,
separate from `DOCS_PASSWORD_HASH`/`JOB_TRACKER_PASSWORD_HASH`/`FAMILY_PASSWORD_HASH`), never
listed on `/projects` or in the footer disclaimer (see the comment above `PROJECTS` in
`app/blueprints/projects/routes.py`), `noindex` on every response (`X-Robots-Tag` header via
`@bp.after_request`), and `Disallow: /projects/trading-bot` in `robots.txt`. The gate is the
actual access control; the hiding just keeps the URL out of search results and casual browsing.

`/research` carries no separate password -- reaching it at all already required unlocking the
overview, so it just re-checks the same session key (same pattern as `/documentation/interview`).

Generate a password hash the same way as the other gates:
```bash
python -c "from werkzeug.security import generate_password_hash as g; print(g(input()))"
```
Set it as `TRADING_BOT_PASSWORD_HASH` in `.env` (remember the `$$`-doubling gotcha for Compose --
Werkzeug hashes contain `$`). `TRADING_BOT_UNLOCK_RATE_LIMIT` (default `10 per hour`) throttles
guessing on the unlock POST specifically, same as the other gates.

## Where the data comes from

Trading Bot has no HTTP API. Its own `scripts/export_stats.py` periodically writes a JSON
snapshot, `public_stats.json`, containing:

- `live` -- current equity/cash/day-change, open positions, pending orders, last rebalance
  timestamps. Changes constantly (well, whenever the bot's daily job runs).
- `research` -- Sharpe, annualized/total return, max drawdown, return-by-year, weekly hit rate,
  and the caveats (`notes`). Static; only changes when new research is done and that block is
  hand-updated in the export script.

This blueprint reads that file from `TRADING_BOT_STATS_PATH` (`app/config.py`), defaulting to
`instance/trading_bot_stats.json` -- **inside this app's own instance folder**, not a path into
the Trading Bot repo. That default only works if something has put a copy of `public_stats.json`
there. Getting it there is a sync problem, not something this blueprint solves itself:

- **Local dev, same machine as the bot:** copy (or symlink) `S:\Tradingbot\public_stats.json` to
  `instance/trading_bot_stats.json` by hand, or point `TRADING_BOT_STATS_PATH` straight at the
  bot's file via `.env`.
- **Production (the Hetzner VPS):** the bot runs on a separate Windows machine, so the file has to
  cross hosts -- e.g. extend the Windows Task Scheduler job (`TradingBotRebalance`,
  `scripts/run_rebalance.bat`) to `scp`/`rsync` the freshly-exported file to the VPS after every
  run, landing it at the container's mounted `instance/` path. Not wired up yet; this is the next
  step for a live production deployment, called out in the original handover doc as an open
  question ("is the site deployed remotely right now").

`app/services/trading_bot.py` reads that file **best-effort and never raises** -- a missing file
(nothing synced yet), a mid-copy/corrupt file, or a stale one (older than
`TRADING_BOT_STALE_AFTER_HOURS`, default 48h) all render as a plain message on the page instead of
a 500 or silently-wrong numbers. Same graceful-degradation contract as
`app/services/market_warehouse.py` uses for its own external-file read -- read that module's
docstring for the reasoning if extending this one.

## Pages

- **`/projects/trading-bot`** -- the "what this is" banner (paper/live mode, real broker
  infrastructure), current equity/day-change/cash/position count, a positions table (falls back to
  showing pending orders when positions are empty, e.g. right after a rebalance before fills
  settle), and last/next-due rebalance dates. "Next due" is a display-only estimate (last
  rebalance + 30 days, the trend sleeve's cadence) -- not read from the bot's own scheduler state.
- **`/projects/trading-bot/research`** -- the methodology paragraph (this replays the *actual
  deployed strategy code* day-by-day, not a vectorized backtest -- see `research.methodology` in
  the snapshot), headline stats, a red/green bar chart of return by year, the weekly hit-rate
  numbers (shown even though ~40% of weeks lose money -- burying that would misrepresent the
  project), and the `notes` caveats verbatim (crypto sleeve disabled on fees, Kronos found no
  edge, survivorship bias in the stock universe).

## Key files

- `app/blueprints/trading_bot/routes.py` -- routes (both just call
  `trading_bot.get_dashboard_data()` and render)
- `app/services/trading_bot.py` -- snapshot file reader, shape normalization, staleness check
- `app/templates/trading_bot/`
- `app/static/js/trading_bot.js` -- the by-year bar chart (Chart.js, loaded from CDN same as
  Market Data Warehouse)

## Tests

`tests/test_trading_bot.py` -- the service's graceful degradation (missing/corrupt/stale file),
shape normalization (by-year dict -> sorted list, position/pending counts), and route smoke tests
against a real temp snapshot file (no live Alpaca calls in CI -- this blueprint never calls Alpaca
directly, only reads the exported file).

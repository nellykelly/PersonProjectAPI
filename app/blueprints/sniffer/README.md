# <img src="../../static/assets/img/icons/sniffer.svg" width="32" height="32" alt=""> Site Traffic Analytics

**Route:** `/projects/network-sniffer` (blueprint/module names kept as `sniffer` -- an
internal rename felt like unnecessary churn for a page that still logs exactly the same
traffic, just reports on it differently)

An analytics board over this application's own network traffic: request volume over
time, latency percentiles, error rate, and the busiest endpoints and outbound calls.

This started as a raw live log (a scrolling table of every request, pushed over SSE the
instant it happened) and was rebuilt into an aggregate analytics board instead -- the
same underlying data, reported as a rollup rather than a stream of individual rows. A raw
log is a good demo of "look, it's live"; an analytics board is closer to what this data is
actually *for*.

## Scope (read this before wondering why it doesn't show visitor traffic)

Capturing arbitrary visitors' network traffic -- their browsing, their packets -- is a
privacy and legal problem: most jurisdictions treat unauthorized traffic interception as
wiretapping regardless of intent, and any hosting provider's ToS will prohibit it. This
applies even on this exact site if a visitor hasn't explicitly consented, because the
traffic in question isn't limited to their interactions with this app.

So this project captures and aggregates **only the app's own traffic**:

1. **Inbound** -- every request this Flask app receives on its own routes, via
   app-wide `before_request`/`after_request` hooks (`app/services/net_monitor.py`).
2. **Outbound** -- every API call the app itself makes (to `yfinance` for the Trading
   Simulator, to SEC EDGAR for the Company Scorer), logged explicitly at the call site in
   `market_data.py` / `edgar.py`.

It never touches a visitor's actual browsing traffic. Static asset requests
(`/static/...`) are filtered out as noise -- everything else is real.

## What the board shows

- **Volume over time** -- inbound/outbound counts bucketed across whatever span of
  traffic the retained window currently holds (a fixed number of equal-width buckets
  spanning the buffer's own oldest-to-newest timestamp, not a fixed wall-clock window --
  a quiet site and a busy one both render sensibly rather than mostly-empty buckets).
- **Latency percentiles** -- p50/p90/p99 and max, computed separately for inbound and
  outbound, by a hand-rolled linear-interpolation percentile function (the same "don't
  reach for numpy over one small stats function" call already made for the Company Scorer's
  backtest correlation). p50 alone hides the slow tail that actually matters for "is this
  page ever slow"; p99 is what surfaces it.
- **Error rate** -- inbound requests answered 4xx vs 5xx, tracked and shown separately,
  since a wave of "not found"s and a wave of server errors mean very different things.
- **Top endpoints** -- inbound requests grouped by **Flask endpoint name**
  (`trading.position_detail`), not raw path. Grouping by raw path would fragment every
  dynamic route into its own row (`/positions/7`, `/positions/8`, ... each separate)
  instead of one meaningful "this route gets hit a lot" row -- endpoint is logged
  alongside the path specifically so aggregation has something coarser to group by.
- **Top outbound hosts** -- reuses the same host-grouping logic as before (see below).

## Implementation

- A thread-safe, bounded in-memory ring buffer (`collections.deque`) -- not persisted to
  the database. This is a rollup over recent traffic, not an audit trail; resetting on
  restart is an acceptable, simpler trade-off at this scope.
- `GET /api/analytics` returns the full computed board (stats, latency, error rate, top
  endpoints, top hosts, volume buckets) fresh from the current buffer -- no caching, since
  computing it is cheap (plain Python over at most 500 entries) and correctness ("this
  reflects traffic right now") matters more than shaving a few milliseconds.
- The page polls that endpoint every 5 seconds and re-renders in place -- a plain `fetch`
  + `setInterval`, not a persistent connection. An aggregate rollup doesn't need a push
  per individual entry the way the old raw log did, so there's no SSE stream, no
  per-viewer subscriber queue, and no SSE concurrency cap to enforce here anymore (see
  `app/services/sse_limits.py`, which now only guards the Trading Simulator's risk-feed
  and watchlist streams) -- one fewer moving part for the same visible "this is live"
  effect, since 5-second staleness is unnoticeable for a board of aggregates.
- Pseudo-URL host grouping is unchanged from the original live-log version:
  `market_data.py` logs yfinance calls as e.g. `yfinance://AAPL/info` (yfinance manages
  its own HTTP client, so there's no real URL to log), and a naive `target.split("/")[2]`
  would misread the *ticker* as the host. Real `http(s)://` targets group by hostname;
  anything else groups by its scheme name instead.

## Try it

Open the Trading Simulator or Company Scorer in another tab and generate some traffic (open a
position, run a risk request, score a ticker) -- come back here and the counters, top
endpoints, and volume chart pick it up on the next poll.

## Key files

- `app/blueprints/sniffer/routes.py`
- `app/services/net_monitor.py`
- `app/templates/sniffer/`, `app/static/js/sniffer.js`

## Tests

`tests/test_sniffer.py` -- confirms inbound requests are logged and grouped by endpoint
(not raw path), outbound calls carry their source/timing, the host-breakdown groups
pseudo-URLs by scheme correctly, 4xx/5xx are counted separately, and the percentile/
volume-bucket math itself (a single value, an even split, a zero-length time span that
must not divide by zero).

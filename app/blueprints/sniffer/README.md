# Network Sniffer

**Route:** `/projects/network-sniffer`

A live dashboard of this application's own network traffic.

## Scope (read this before wondering why it doesn't show visitor traffic)

Capturing arbitrary visitors' network traffic -- their browsing, their packets -- is a
privacy and legal problem: most jurisdictions treat unauthorized traffic interception as
wiretapping regardless of intent, and any hosting provider's ToS will prohibit it. This
applies even on this exact site if a visitor hasn't explicitly consented, because the
traffic in question isn't limited to their interactions with this app.

So this project captures and visualizes **only the app's own traffic**:

1. **Inbound** -- every request this Flask app receives on its own routes, via
   app-wide `before_request`/`after_request` hooks (`app/services/net_monitor.py`).
2. **Outbound** -- every API call the app itself makes (to `yfinance` for the Trading
   Simulator, to SEC EDGAR for the QR Scorer), logged explicitly at the call site in
   `market_data.py` / `edgar.py`.

It never touches a visitor's actual browsing traffic. Static asset requests
(`/static/...`) are filtered out of the log as noise -- everything else is real.

## Implementation

- A thread-safe, bounded in-memory ring buffer (`collections.deque`) -- not persisted to
  the database. This is a live view, not an audit trail; resetting on restart is an
  acceptable, simpler trade-off at this scope.
- `/api/log` returns the last 200 entries (method, path/host, status, duration) plus
  aggregate stats (counts by direction, average latency, outbound calls by host); the
  dashboard polls it every 4 seconds.

## Try it

Open the Trading Simulator or QR Scorer in another tab and watch their outbound
`yfinance`/SEC EDGAR calls show up here in real time, alongside the inbound requests for
the pages you're browsing.

## Key files

- `app/blueprints/sniffer/routes.py`
- `app/services/net_monitor.py`
- `app/templates/sniffer/`, `app/static/js/sniffer.js`

## Tests

`tests/test_sniffer.py` -- confirms inbound requests get logged, outbound calls carry
their source/timing, and the host-breakdown stats aggregate correctly.

---
title: "Project: Site Traffic Analytics"
kind: project
---

# What it is

An analytics board at /projects/network-sniffer over this application's own network
traffic: request volume over time, latency percentiles, error rate, and the busiest
endpoints and outbound calls. It started as a raw live log — a scrolling table of every
request pushed over SSE — and was rebuilt into an aggregate board, which is closer to
what the data is actually for.

# Scope, and why it does not show visitor browsing

Capturing arbitrary visitors' network traffic is treated as wiretapping in most
jurisdictions regardless of intent, and would violate almost any host's terms of
service. So this project captures and aggregates **only the app's own traffic**: inbound
requests to its own routes, via app-wide `before_request` and `after_request` hooks, and
outbound API calls the app itself makes to `yfinance` and SEC EDGAR, logged at the call
site. It never touches a visitor's actual browsing. Static-asset requests are filtered
out as noise.

# What the board shows

- **Volume over time** — inbound and outbound counts bucketed across whatever span the
  retained buffer holds, so a quiet site and a busy one both render sensibly.
- **Latency percentiles** — p50, p90, p99 and max, computed separately for inbound and
  outbound by a small hand-rolled linear-interpolation percentile function. p50 alone
  hides the slow tail that actually matters.
- **Error rate** — 4xx versus 5xx, tracked separately, because a wave of "not found"s
  and a wave of server errors mean very different things.
- **Top endpoints** — grouped by Flask endpoint name, not raw path, so a dynamic route
  is one meaningful row instead of one row per id.

# Implementation

A thread-safe, bounded in-memory ring buffer, not persisted to the database — this is a
rollup over recent traffic, not an audit trail, so resetting on restart is an acceptable
simplification. The page polls a server-computed analytics endpoint every few seconds; a
five-second staleness is unnoticeable for a board of aggregates, so there is no SSE
stream to maintain.

# What it demonstrates

Middleware instrumentation, a sensible fixed-memory data structure, percentile
statistics without reaching for a heavy dependency, and a clear-eyed legal and privacy
scoping decision.

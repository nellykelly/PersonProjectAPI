---
title: "Project: Trading Simulator / PnL Tracker"
kind: project
---

# What it is

A booking-style simulator at /projects/trading-simulator. A visitor opens a simulated
stock or option position on a whitelisted ticker and watches its profit-and-loss move
against real, delayed market data. There is no login: it is a shared, anonymous, public
trade book, so every position anyone opens is visible to everyone. It is a simulation
only, and nothing on it is financial advice.

# How it works

Prices, historical series, and option chains come from `yfinance`. `yfinance` does not
provide option Greeks, so option positions are repriced locally with a Black-Scholes
calculation using the implied volatility captured at entry and the current underlying
price.

The booking model follows how a real trading desk separates concerns rather than using
one flat row per trade: an `Instrument` is reference data for one specific contract
(deduplicated, so everyone trading the same AAPL $230 call shares one row); a `Strategy`
is a named container holding one or more `Leg`s; a `Leg` is one booked transaction.
Every instrument gets a real industry-standard OCC option symbol and there is a
searchable instrument catalogue.

# Risk engine

Risk is computed by explicitly submitting a `RiskRequest` — against one leg, a whole
position, or the entire book — which returns a persisted result, so "every risk run,
against what, and what it found" is a queryable fact. The pricing math includes real
Black-Scholes delta, gamma, theta, vega and rho, quoted the way traders read them, plus
a scenario (bump-and-revalue) convexity and a small Hull-White stochastic-rate extension
used only to derive interest-rate vega.

There are two pluggable risk models — a fast closed-form one and a full-revalue one that
reprices across a spot ladder — behind a shared interface, so results under different
models are directly comparable.

Risk pricing runs on a **separate worker process**, not inline in the web request: the
request enqueues a job on a dedicated Redis queue and blocks until the worker finishes
it. Because a worker cannot raise an exception back across a process boundary, it writes
a plain-text failure reason onto the request row and the web side re-derives the right
exception type from it.

# Live data

A live watchlist grid polls all whitelisted tickers on the server and pushes updates to
the browser over Server-Sent Events, but only while a tab is actually connected and only
during market hours, to avoid wasting the free `yfinance` rate-limit budget. Clicking
tickers adds them to a multi-stock live chart.

# What it demonstrates

Domain modelling of a real financial system, numerical methods (Black-Scholes and
bump-and-revalue), distributed work over a queue and a separate worker, and careful
failure handling across a process boundary.

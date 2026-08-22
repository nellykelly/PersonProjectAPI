# Build Prompt: Nelson Koskela Personal Website

## Context
Build a personal portfolio website for a software engineer (JPMorgan Chase & Co., BS Computer Science). The site should be built in **Flask (Python)**, containerized with **Docker**, and showcase both professional background and independent technical projects. This is a portfolio piece meant to impress technical recruiters/hiring managers, so code quality and clean architecture matter as much as the final UI.

---

## 1. Site Structure

- **Home/Landing page** — short intro, nav to About / Projects / Contact, links to resume + GitHub + LinkedIn
- **About page/section** — professional bio (content below), resume download link
- **Projects section** — landing page listing all 3 projects, each with its own subpage
- **Contact section** — email link, LinkedIn, GitHub at minimum; contact form is a stretch goal
- Use **Flask blueprints** — one per major section (`about`, `projects`, `contact`) rather than a single monolithic `app.py`
- Routing: `/`, `/about`, `/projects`, `/projects/trading-simulator`, `/projects/qr-quant-scraper`, `/projects/network-sniffer`

## 2. About Section Content (use as-is or adapt)

Bio: Software Engineer at JPMorgan Chase & Co. (Houston, TX) since July 2022, started as SEP intern in 2021. BS Computer Science from Cogswell University of Silicon Valley (started at Howard University). Trained in classical vocal performance at Ruth Asawa School of the Arts. Blends technical and creative background. Builds independent projects in trading systems, financial data pipelines, and network diagnostics.

Contact links needed: Resume PDF (static file), LinkedIn (https://www.linkedin.com/in/nelson-k-70180a101), Email (koskela.nelson@gmail.com), GitHub (https://github.com/nellykelly).

> **Why GitHub goes site-wide:** portfolio sites conventionally put GitHub/LinkedIn/resume/email in a persistent header or footer nav, visible on every page — not just the About page. A recruiter who lands directly on a Projects subpage (e.g., from a shared link) should still be one click from your code without navigating back to About first.

## 3. Project 1: Trading Simulator / PnL Tracker

**Concept:** A booking-style site that simulates buying an option on a ticker and tracks how its value changes over time, with PnL and a live/historical graph of the outcome.

**Requirements:**
- **Data source: `yfinance`** (confirmed). Use for underlying price data (historical + near-real-time via polling).
  - Note: `yfinance` does expose an **options chain** (strikes, expiries, bid/ask, open interest, implied volatility) via `Ticker.option_chain()`, but it does **not** provide computed Greeks (delta, theta, gamma, etc.). If Greeks are wanted for the PnL graph, they'll need to be calculated locally (e.g., Black-Scholes using the IV yfinance provides).
  - Data is delayed (typically ~15 min), not real-time — fine for a portfolio project, should be labeled as such in the UI.
- **No login/accounts** — this is a shared, public trade book. Anyone visiting the site can book a simulated trade, and all booked trades are visible to all visitors.
  - Because it's open/anonymous, add basic abuse protection: rate-limit trade submissions per IP, cap position size/count per session, and sanitize all inputs (ticker symbol validation against a known list before hitting yfinance).
- Let a user "open a position" on a ticker (stock or option, per above)
- Track position over time, compute PnL
- Aggregate pulled market data into a "position" object, then generate a report from that position for the visualizer
- Build order: **first a working/correct solution, then optimize for speed**
- Deliverables: standard historical reports + a live-ticking graph view (polling yfinance on an interval, since it's not a true streaming source)
- Needs persistence (SQLite is a natural fit with Flask) to track all open/closed positions, shared across all visitors
- **This is a simulation only — must include a clear "not financial advice / simulated data / delayed data" disclaimer**

## 4. Project 2: QR — Quant Company Scorer

**Concept:** Scrapes a company's public reports/filings and data, runs quant calculations, and outputs a score of "how good" the company is, including some validation/quant-calculation component.

**Requirements:**
- Define the specific data sources being scraped (e.g., SEC EDGAR filings, public financial statement APIs) — **do not scrape sites that disallow it in robots.txt or ToS**
- **Scoring methodology: multiple calculations across categories** (confirmed) — build the score as a composite across at least these factor groups, each contributing to a sub-score, rolled into an overall score:
  - **Valuation**: P/E ratio, P/B ratio, EV/EBITDA
  - **Leverage/solvency**: debt-to-equity, current ratio, interest coverage
  - **Growth**: revenue growth (YoY/QoQ), earnings growth
  - **Profitability**: margins (gross/operating/net), ROE, ROA
  - Weight each category explicitly (even a simple equal-weight average is fine as v1) and make the weighting configurable, not hardcoded, so it can be tuned later
- **Validation = backtesting**: test whether a high score historically correlated with better forward stock performance (e.g., score the company using data as of 1 year ago, compare to actual price return since then). This is the "quant calculation on validation" piece — it's a separate module that runs the scoring formula against historical data and reports how predictive it's been.
- Should include a disclaimer that scores are not investment advice

## 5. Project 3: Network Sniffer

**Concept:** A page/tool showing network traffic through the site with a breakdown of what each request/connection is doing.

**Requirements:**
- **Important scoping constraint:** capturing arbitrary visitors' network traffic (their browsing, their packets) is a privacy and legal problem — most jurisdictions treat unauthorized traffic interception as wiretapping regardless of intent, and any hosting provider's ToS will prohibit it. This applies even on your own site if visitors haven't explicitly consented, because the traffic in question isn't limited to their interactions with your app.
- **Recommended, legally-safe scope**: capture and visualize the Flask app's *own* traffic — the outbound API calls it makes (to yfinance, SEC EDGAR, etc. for Projects 1 & 2) and the inbound requests it receives from visitors hitting its own routes. This still delivers "see all network traffic going through the site" in a meaningful way — it shows exactly what your app is doing over the network — without touching any visitor's actual browsing traffic.
- Implementation: instrument Flask request/response logging as middleware (in/out) plus log every outbound `requests`/`yfinance` call the app makes, and visualize that combined log as a live breakdown/timeline.
- If true packet-level capture is wanted for demo purposes, scope it to a sandboxed container capturing only its own loopback/internal traffic — never a visitor-facing capture.

## 6. Cross-Cutting Requirements

- **Docker**: every project should run via Docker; use `docker-compose` for multi-service setups (Flask app + SQLite/Postgres + any background worker for live ticking data)
- **Data sources**: name concrete APIs (e.g., yfinance, Alpha Vantage, FRED, SEC EDGAR) — free tiers have rate limits that will affect the "live ticking graph" requirement
- **Disclaimers**: financial simulation and company-scoring projects need "not financial advice" language, ideally footer-wide
- **Hosting target**: needs to be decided (Render, Fly.io, a VPS — GitHub Pages will NOT work since this is a Flask app, not static)
- **Testing**: at minimum basic route/unit tests for each blueprint
- **README per project**: setup instructions, what it does, tech stack, screenshots/GIF if possible

## 7. Base Repo & Design Template

**Repo to use as the project base:** `https://github.com/nellykelly/PersonProjectAPI.git`

This repo is expected to contain everything needed to bootstrap the site, including multiple template options. **Use the dark-themed template. Do not use any pink/light-colored template that may also be present in the repo.** If the repo turns out to be missing something referenced elsewhere in this spec, flag it rather than silently substituting something else.

## Decisions Confirmed

1. **GitHub URL** — `https://github.com/nellykelly` — wired site-wide in header/footer nav.
2. **Trading Simulator data source** — `yfinance`, confirmed. Stock data plus its options-chain endpoint (no Greeks included; compute locally if needed).
3. **QR scoring methodology** — composite multi-factor score (valuation, leverage, growth, profitability) with configurable weights, validated via historical backtesting. See spec above.
4. **Network Sniffer scope** — captures the app's own network traffic (its outbound API calls + inbound visitor requests to its own routes), not visitors' actual browsing traffic. This is a hard legal/privacy constraint, not a style preference — see note above.
5. **Hosting** — undecided, to be explored during build. Keep `docker-compose` and env var config provider-agnostic (12-factor style) so switching between Render/Fly.io/a VPS later is low-friction.
6. **Auth** — none. Trading simulator is a public, anonymous, shared trade book — anyone can book a trade and all trades are visible to everyone. Requires rate-limiting and input validation to prevent abuse (see Project 1 notes).
7. **Design direction** — use the dark-themed template from `https://github.com/nellykelly/PersonProjectAPI.git`, not the pink one.

## Remaining Open Items

- Hosting target (can be decided later, doesn't block initial build)
- Confirm the dark template in the repo is the intended one once the build AI inspects it directly (repo contents weren't independently viewable when this spec was written)

Build order suggestion: scaffold the Flask app + About/Contact pages using the provided dark-themed template first (quick win, portfolio-ready immediately), then Trading Simulator (most complex, most impressive), then QR, then Network Sniffer (smallest scope, ties the others together by visualizing their own API traffic).

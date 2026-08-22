# Nelson Koskela -- Personal Site

Personal portfolio site for Nelson Koskela, Software Engineer at JPMorgan Chase & Co.
Built with Flask (blueprints, one per section/project), SQLite, and Docker, per
[`docs/build-spec.md`](docs/build-spec.md).

- **GitHub:** https://github.com/nellykelly
- **LinkedIn:** https://www.linkedin.com/in/nelson-k-70180a101
- **Email:** koskela.nelson@gmail.com

> Every page carries GitHub/LinkedIn/resume/email links in the header and footer, and a
> site-wide disclaimer: nothing on this site is financial advice. The Trading Simulator and
> QR Scorer specifically use simulated/delayed data and are not investment advice.

## What's here

| Section | Route | What it does |
|---|---|---|
| Home | `/` | Intro + links to the rest of the site |
| About | `/about` | Bio, resume download |
| Projects | `/projects` | Landing page for the three projects below |
| [Trading Simulator](app/blueprints/trading/README.md) | `/projects/trading-simulator` | Shared, anonymous public trade book -- open a simulated stock/option position and track PnL against live-polled `yfinance` data |
| [QR -- Quant Company Scorer](app/blueprints/qr/README.md) | `/projects/qr-quant-scraper` | Scores a company on valuation/leverage/growth/profitability from SEC EDGAR + market data, with a backtest module |
| [Network Sniffer](app/blueprints/sniffer/README.md) | `/projects/network-sniffer` | Live dashboard of this app's own inbound requests and outbound API calls |
| Contact | `/contact` | Email / LinkedIn / GitHub |

## Tech stack

Flask 3 (application-factory + blueprints), Flask-SQLAlchemy (SQLite), Flask-Limiter,
`yfinance`, SEC EDGAR's public `data.sec.gov` API, vanilla JS + Chart.js (CDN) on the
frontend, gunicorn + Docker for deployment. See [`requirements.txt`](requirements.txt).

## Running it

### Docker (recommended)

```bash
cp .env.example .env   # fill in a real SECRET_KEY, etc.
docker compose up --build
```

Then visit http://localhost:8000.

### Locally (no Docker)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
flask --app wsgi run
```

`gunicorn` (used by the Dockerfile) doesn't run on Windows -- use `flask --app wsgi run`
or `python wsgi.py` for local Windows development instead.

### Tests

```bash
pytest
```

All 34 tests run offline -- external calls (`yfinance`, SEC EDGAR) are monkeypatched in
tests, since both are rate-limited/flaky on live, unauthenticated use and shouldn't gate CI.

## Project structure

```
app/                  Flask package (application factory in app/__init__.py)
  blueprints/          main, about, contact, projects, trading, qr, sniffer
  services/            market_data.py, pricing.py, edgar.py, quant_score.py, backtest.py, net_monitor.py
  static/ templates/    Dark theme (HTML5 UP "Dimension", see below) + custom CSS/JS
tests/                 pytest, one file per blueprint/service
docs/                  build-spec.md (original spec), SECURITY-NOTE.md
legacy/                Retired code from the original repo (blog, Google Calendar
                        integration, the licensed "pink" Colorlib template) -- kept for
                        history, not part of the running app. See legacy/README.md.
```

## Design

The dark, one-page "Dimension" template already in this repo
(`app/templates` originally had it as `indexp.html`) was rebuilt into a proper multi-page
`base.html` + per-blueprint templates, per the build spec's instruction to use the dark
template and not the pink one (a separately *licensed* Colorlib education template that
also lived in this repo -- see `legacy/README-colorlib-license.txt`). Design credit:
[HTML5 UP](https://html5up.net) (Dimension, CCA 3.0).

## Known local-environment quirk (not a code bug)

If you're testing on a Windows machine with Avast (or similar antivirus doing HTTPS
scanning) and see `SSL: CERTIFICATE_VERIFY_FAILED` errors from the Trading Simulator or
QR Scorer, that's the antivirus's local TLS interception, not this app or a network
outage -- Python's certificate bundle doesn't trust Avast's injected root the way the OS
does. It doesn't affect Docker/Linux hosting. Both `market_data.py` and `edgar.py` treat
this the same as any other data-source failure: a clean, non-crashing error state, per
the graceful-degradation design called for in the build spec.

## Hosting

Left open per the build spec -- config is 12-factor (env vars, no hardcoded provider
assumptions), so Render, Fly.io, or a plain VPS all work without code changes.

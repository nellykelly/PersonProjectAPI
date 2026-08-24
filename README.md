# Nelson Koskela -- Personal Site

Personal portfolio site for Nelson Koskela, Software Engineer II at JPMorgan Chase & Co.,
Corporate & Investment Banking. Built with Flask (blueprints, one per section/project),
Docker, and (for Pipeline World) Redis + Postgres, per [`docs/build-spec.md`](docs/build-spec.md)
and [`docs/build-spec-pipeline-world.md`](docs/build-spec-pipeline-world.md).

- **GitHub:** https://github.com/nellykelly
- **LinkedIn:** https://www.linkedin.com/in/nelson-k-70180a101
- **Email:** koskela.nelson@gmail.com

> Every page carries GitHub/LinkedIn/resume/email links in the header and footer, and a
> site-wide disclaimer: nothing on this site is financial advice. The Trading Simulator and
> Company Scorer specifically use simulated/delayed data and are not investment advice.

## What's here

| Section | Route | What it does |
|---|---|---|
| Home | `/` | Intro + links to the rest of the site |
| About | `/about` | Bio, resume download |
| Projects | `/projects` | Landing page for the six projects below + an "Earlier Projects" archive |
| [Trading Simulator](app/blueprints/trading/README.md) | `/projects/trading-simulator` | Shared, anonymous public trade book -- open a simulated stock/option position and track PnL against live-polled `yfinance` data; run risk (pluggable quant models) against one leg, a whole position, or the whole book, priced on a separate worker via Redis/RQ; an instrument catalog lookupable by OCC option code; a live watchlist grid with a click-to-plot multi-stock chart |
| [Company Scorer](app/blueprints/qr/README.md) | `/projects/qr-quant-scraper` | Scores a company on valuation/leverage/growth/profitability from SEC EDGAR + market data, with a backtest module |
| [Pipeline World](app/blueprints/pipeline_world/README.md) | `/projects/pipeline-world` | Submit a character, watch it move through a real, queued CI/CD-style pipeline live in a top-down world; a Postgres SQL analytics page over every run |
| [SRE Infra Layer](app/blueprints/sre_infra/README.md) | `/projects/sre-infra` | The Redis queue/cache-aside/rate-limit infrastructure underneath Pipeline World, dashboarded |
| [Site Traffic Analytics](app/blueprints/sniffer/README.md) | `/projects/network-sniffer` | An analytics board over this app's own inbound requests and outbound API calls -- volume over time, latency percentiles, error rate, busiest endpoints/hosts |
| [Timed-Squares](app/blueprints/timed_squares/README.md) | `/projects/timed-squares` | A turn-based survival game on a 10x10 grid, playable in-browser (HTML5 Canvas) -- dodge obstacles that telegraph their next move before they make it, with a public leaderboard |
| Contact | `/contact` | Email / LinkedIn / GitHub |

## Tech stack

Flask 3 (application-factory + blueprints), Flask-SQLAlchemy, Flask-Limiter, Flask-SocketIO,
RQ + Redis, Postgres (Pipeline World; Trading Simulator's position/risk data works against
either) / SQLite (everything else works against either), `yfinance`, SEC EDGAR's public
`data.sec.gov` API, vanilla JS + Chart.js (CDN) + Socket.IO client (CDN) on the frontend,
gunicorn + Docker for deployment. See [`requirements.txt`](requirements.txt).

One RQ worker pool (`worker.py`, its own container) now does two jobs: Pipeline World's
character-join pipeline, and the Trading Simulator's risk pricing -- a risk request is
priced on that separate worker process, not inline in the web request that asked for it
(see [SRE Infra Layer](app/blueprints/sre_infra/README.md) and
[Trading Simulator](app/blueprints/trading/README.md)).

## Running it

### Docker (recommended -- required for the full stack)

```bash
cp .env.example .env   # fill in a real SECRET_KEY, etc.
docker compose up --build
```

Then visit http://localhost:8000. This starts four services: `web`, `worker` (the RQ
worker consuming Pipeline World's queue), `postgres`, and `redis`.

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

Without Docker, `REDIS_URL`/`DATABASE_URL` are unset, so both Pipeline World's pipeline
and the Trading Simulator's risk pricing fall back to an in-memory `fakeredis` instance
with a worker thread inside the web process (still genuinely asynchronous -- see
[`app/services/queue.py`](app/services/queue.py)) and SQLite. Everything works this way
**except** `/projects/pipeline-world/pipeline-analytics`, which specifically requires a
live Postgres connection (its SQL uses `DATE_TRUNC` and window functions) -- point
`DATABASE_URL` at your own Postgres to exercise it locally, or just use Docker.

### Tests

```bash
pytest
```

The full suite runs offline -- external calls (`yfinance`, SEC EDGAR) are monkeypatched,
and Pipeline World's queue/cache run against `fakeredis` with RQ in synchronous mode, so
a full character join runs to completion inline with no sleeps or polling. A handful of
Postgres-specific analytics-correctness tests auto-skip here and run for real once
pointed at a live Postgres (i.e. via `docker-compose`).

## Project structure

```
app/                  Flask package (application factory in app/__init__.py)
  blueprints/          main, about, contact, projects, trading, qr, sniffer,
                       pipeline_world, sre_infra, timed_squares
  services/            market_data.py, pricing.py, instruments.py, risk_engine.py,
                       risk_models/, risk_dashboard.py, edgar.py, quant_score.py,
                       backtest.py, net_monitor.py, watchlist.py, validators.py,
                       pipeline.py, queue.py, world_cache.py, analytics.py
  static/ templates/    Dark theme (HTML5 UP "Dimension", see below) + custom CSS/JS
tests/                 pytest, one file per blueprint/service
docs/                  build-spec.md, build-spec-pipeline-world.md (original specs), SECURITY-NOTE.md,
                        INTERVIEW-NOTES.md (personal reference: decisions/tradeoffs/bugs worth
                        discussing in an interview -- not linked from the live site)
legacy/                Retired code from the original repo (blog, Google Calendar
                        integration, the licensed "pink" Colorlib template) -- kept for
                        history, not part of the running app. See legacy/README.md.
worker.py              RQ worker entrypoint (its own container in docker-compose)
```

## Design

The dark, one-page "Dimension" template already in this repo
(`app/templates` originally had it as `indexp.html`) was rebuilt into a proper multi-page
`base.html` + per-blueprint templates, per the build spec's instruction to use the dark
template and not the pink one (a separately *licensed* Colorlib education template that
also lived in this repo -- see `legacy/README-colorlib-license.txt`). Design credit:
[HTML5 UP](https://html5up.net) (Dimension, CCA 3.0). Project icons are hand-drawn SVGs
(`app/static/assets/img/icons/`), not stock art.

## Known local-environment quirk (not a code bug)

If you're testing on a Windows machine with Avast (or similar antivirus doing HTTPS
scanning) and see `SSL: CERTIFICATE_VERIFY_FAILED` errors from the Trading Simulator or
Company Scorer, that's the antivirus's local TLS interception, not this app or a network
outage -- Python's certificate bundle doesn't trust Avast's injected root the way the OS
does. It doesn't affect Docker/Linux hosting. Both `market_data.py` and `edgar.py` treat
this the same as any other data-source failure: a clean, non-crashing error state, per
the graceful-degradation design called for in the build spec.

## Hosting

Live at **https://nelsonkoskela.dev** -- a Hetzner CX22 VPS running the full
`docker-compose.yml` stack (`web`, `worker`, `postgres`, `redis`, `caddy`) as five
containers on one box. `caddy` is the only service reachable from the public internet
(ports 80/443, everything else stays on the internal Docker network); it terminates
automatic Let's Encrypt HTTPS for both the bare domain and `www`, and serves everything
under `/static/*` directly off disk via its own `file_server` rather than proxying static
assets through to gunicorn -- gunicorn runs as a single process with a small fixed thread
pool (deliberately, for Flask-SocketIO session affinity -- see
[Pipeline World](app/blueprints/pipeline_world/README.md)), so routing 15-20 static
requests per page load through that same pool was a real, measured bottleneck, not just
theoretical. Left 12-factor either way (env vars, no hardcoded provider assumptions), so
this specific choice of host isn't load-bearing -- Render, Fly.io, or any other VPS would
work without code changes, as long as it can also run the Postgres/Redis/worker services
docker-compose defines.

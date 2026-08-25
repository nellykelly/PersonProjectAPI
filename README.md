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
| Projects | `/projects` | Landing page for the five active projects below + an "Earlier Projects" archive |
| [Company Scorer](app/blueprints/qr/README.md) | `/projects/qr-quant-scraper` | Scores a company on valuation/leverage/growth/profitability from SEC EDGAR + market data, with a backtest module |
| [Pipeline World](app/blueprints/pipeline_world/README.md) | `/projects/pipeline-world` | Submit a character, watch it move through a real, queued CI/CD-style pipeline live in a top-down world; a Postgres SQL analytics page over every run |
| [SRE Infra Layer](app/blueprints/sre_infra/README.md) | `/projects/sre-infra` | The Redis queue/cache-aside/rate-limit infrastructure underneath Pipeline World, dashboarded |
| [Site Traffic Analytics](app/blueprints/sniffer/README.md) | `/projects/network-sniffer` | An analytics board over this app's own inbound requests and outbound API calls -- volume over time, latency percentiles, error rate, busiest endpoints/hosts |
| [Timed-Squares](app/blueprints/timed_squares/README.md) | `/projects/timed-squares` | A turn-based survival game on a 10x10 grid, playable in-browser (HTML5 Canvas) -- dodge obstacles that telegraph their next move before they make it, with a public leaderboard |
| Documentation | `/documentation` | A long-form engineering reference for this codebase -- architecture, data model, every subsystem, UML/sequence diagrams. Its own stylesheet, not part of the site's dark theme. |
| Documentation &rarr; Interview questions | `/documentation/interview` | Password-gated section of the reference above (`DOCS_PASSWORD_HASH`) -- an interview-prep question bank keyed to the codebase. Fails closed if unconfigured; never linked or indexed. |
| Contact | `/contact` | Email / LinkedIn / GitHub |
| [Trading Simulator](app/blueprints/trading/README.md) *(on hold)* | `/projects/trading-simulator` | Shared, anonymous public trade book -- open a simulated stock/option position and track PnL against live-polled `yfinance` data; run risk (pluggable quant models) against one leg, a whole position, or the whole book, priced on a separate worker via Redis/RQ; an instrument catalog lookupable by OCC option code; a live watchlist grid with a click-to-plot multi-stock chart. **On hold**: the routes still work, but the project is deliberately unlisted -- not on `/projects`, not on the home page, not in the footer disclaimer. Reachable only by direct link. See `app/blueprints/projects/routes.py: listed_projects()`. |

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

Live at **https://nelsonkoskela.dev** -- a Hetzner CX22 VPS (2 vCPU / 4GB, ~$5-6/mo)
running the full `docker-compose.yml` stack (`web`, `worker`, `postgres`, `redis`,
`caddy`) as five containers on one box. Left 12-factor throughout (env vars, no
hardcoded provider assumptions), so this specific host isn't load-bearing -- Render,
Fly.io, or any other VPS would work without code changes, as long as it can also run
the Postgres/Redis/worker services `docker-compose.yml` defines.

### Network path

Only `caddy` is reachable from the public internet, on 80/443. Everything else
(`web`, `worker`, `postgres`, `redis`) stays on the internal Docker network and is
never exposed -- enforced two ways, not one: the Hetzner Cloud Firewall (console-level,
in front of the VPS entirely) and `ufw` on the host itself both allow only 22/80/443
inbound. `web`'s port 8000 stays published in `docker-compose.yml` for local dev
(`docker compose up` on a laptop still works), but production exposure is controlled
by the firewall layers, not by removing that mapping -- one compose file serves both
environments.

### DNS and TLS

Domain (`nelsonkoskela.dev`) is on Porkbun, with plain A records for both the bare
domain and `www` pointed at the VPS's IP -- a CNAME can't target a raw IP, which is
why both need their own A record rather than `www` aliasing the apex. Caddy requests,
installs, and auto-renews a real Let's Encrypt certificate for both names with no
manual certbot/cron setup:

```
nelsonkoskela.dev, www.nelsonkoskela.dev {
    handle_path /static/* { root * /srv/static; file_server; header Cache-Control "no-cache" }
    reverse_proxy web:8000
}
```

One coupling worth knowing if this ever needs debugging again: a single certificate
covering two domains fails as one unit -- if `www`'s DNS isn't correct yet, its ACME
validation failure drags the *whole order* down to Let's Encrypt's untrusted staging
CA, so the bare domain (whose DNS was fine) ends up serving an untrusted cert too.
Diagnosed by checking the actual issuer, not the padlock icon:
```bash
openssl s_client -connect nelsonkoskela.dev:443 </dev/null 2>/dev/null | openssl x509 -noout -issuer
# looking for O=Let's Encrypt, not a "Fake LE" staging root
```
Fix is to get each domain's DNS correct before adding it to the Caddy site block, not
after.

### Why static assets are served by Caddy, not gunicorn

`gunicorn` runs as a **single process** with 8 threads total (`-w 1 --worker-class
gthread --threads 8` in the Dockerfile) -- deliberately one process, because
Flask-SocketIO's `threading` async mode keeps a client's session in the memory of
whichever process created it; a second gunicorn process would round-robin a session's
later requests onto a process that's never heard of it and return `400 unknown
session`, silently breaking Pipeline World's live updates.

That makes the 8 threads a real, finite, shared budget -- and routing all ~15-20
static CSS/JS/font/icon requests a single page load fires through that same pool,
competing with any other visitor's already-open SSE stream (watchlist, traffic
board) holding a thread indefinitely, was a measured multi-second bottleneck on
*every* load, not just a cold cache. Caddy's `handle_path /static/* { file_server }`
block answers those requests directly off a read-only bind mount of `app/static`,
entirely off the app process.

**Cache-Control on those assets is `no-cache`, not `max-age=...`.** Filenames here
carry no content hash, so nothing about a deploy tells a browser its copy is stale --
`max-age=3600` meant a visitor could keep running the *previous* JS for up to an hour
after a fix shipped (caught directly, debugging a fix that looked broken only because
the browser was serving a cached copy of the old file). `no-cache` still lets the
browser store the file and revalidate with `If-None-Match`; Caddy answers `304` from
the ETag it already sends, so an unchanged asset costs one small conditional request,
not a re-download.

### Deploy loop

```bash
git pull
docker compose up -d --build                 # rebuilds changed layers, recreates changed services
docker compose exec -T web flask db upgrade   # only when a migration landed
```

`--build` targets can be scoped to one service (`... up -d --build web`) for a
faster iteration loop, but the full unscoped form is the default -- worth rebuilding
everything when in doubt, since `web` and `worker` share one image and a
service-scoped rebuild has caused drift before. `--force-recreate` (not `--build`)
is what's needed when only `.env` changed and no image layer did -- Compose can
otherwise reasonably decide nothing needs recreating and leave the previous
environment in place.

### A `.env` gotcha worth knowing before it happens again

Docker Compose interpolates `$` inside `.env`. Adding a password hash
(`DOCS_PASSWORD_HASH`, Werkzeug's `scrypt:N:r:p$salt$digest` format) broke the exact
feature it configured: Compose read `$salt` and `$digest` as undefined variable
references and substituted empty strings, so the container received 16 characters of
a 162-character value. It failed by *looking correctly configured* and rejecting
every password, since a truncated string is still truthy -- not a config file
problem, exactly a wrong-password bug, until the value was checked at the point of
use rather than the point of definition. Fix is Compose's own escape, `$$` for a
literal `$`; worth auditing any secret that isn't plain hex the same way
(`SECRET_KEY`/`POSTGRES_PASSWORD` here are hex, so neither was ever at risk).

### Applying a migration in production

```bash
docker compose exec -T web flask db upgrade
```
Runs Alembic inside the already-built `web` image, against the real `DATABASE_URL`.
The one trap to know: a migration generated against a freshly-built image and copied
to the host, applied *without* rebuilding the image a second time, runs from a build
that predates the file on disk -- `flask db upgrade` reports success while quietly
sitting one migration behind. The safe order has a rebuild on both sides of the
copy, not just the first.

### Rotating the interview-section password

```bash
ssh nelson@<host> 'cd ~/PersonProjectAPI && \
  read -rsp "New password: " P && echo && \
  H=$(docker compose exec -T web python -c "import sys;from werkzeug.security import generate_password_hash as g;print(g(sys.stdin.read().strip()))" <<< "$P") && \
  sed -i "/^DOCS_PASSWORD_HASH=/d" .env && \
  printf "DOCS_PASSWORD_HASH=%s\n" "${H//\$/\$\$}" >> .env && \
  docker compose up -d --force-recreate web'
```
Note the `${H//\$/\$\$}` -- doubling every `$` before it's written to `.env`, for
exactly the reason above.

### What's monitored, and what isn't

Each container declares a `HEALTHCHECK` (`docker compose ps` reports per-service
health), `restart: unless-stopped` brings a crashed service back without
intervention, gunicorn's access/error logs go to stdout (`docker compose logs`), and
the Site Traffic Analytics board tracks p50/p90/p99 latency and 4xx/5xx rates for
the app's own traffic. There's deliberately no external uptime monitor, no alerting,
and no log aggregation past what `docker compose logs` gives you -- reasonable for a
single-owner personal site, and the honest limit to name if this question comes up
(see `docs/INTERVIEW-NOTES.md`).

See [`/documentation`](https://www.nelsonkoskela.dev/documentation) on the live site
(section 29, "Containerisation") for the Dockerfile itself explained line by line --
layer ordering, the non-root user, the healthcheck start-period semantics -- and the
full `docker-compose.yml` service/volume graph as a diagram.

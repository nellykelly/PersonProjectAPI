---
title: Frequently asked questions
kind: faq
---

# What is this site built with?

A single Flask 3 application, organised as one blueprint per section or project
(application-factory pattern). It uses Flask-SQLAlchemy with Postgres (and SQLite for
local development), Redis with RQ for background work, Flask-SocketIO for live updates,
and vanilla JavaScript with Chart.js on the front end. It is containerised with Docker
and deployed with docker-compose.

# How is it deployed?

On a single small Hetzner VPS running the whole docker-compose stack as five containers:
the web app (gunicorn), an RQ worker, Postgres, Redis, and Caddy as the reverse proxy.
Caddy is the only service reachable from the public internet and it requests and renews
its own Let's Encrypt TLS certificate automatically. The domain is nelsonkoskela.dev.

# Is the trading simulator real money?

No. Everything on the site that touches markets is a simulation. The Trading Simulator
is a shared, anonymous, public trade book with no login and no real funds. Prices come
from the free `yfinance` feed and are delayed by roughly fifteen minutes. The Company
Scorer is an educational demo built from public filings. Nothing on the site is
financial advice.

# Can I see the code?

Yes. The source is on GitHub at https://github.com/nellykelly, and the site has a
long-form engineering reference at /documentation that walks through the architecture,
data model, and every subsystem.

# How can I get in touch?

Email koskela.nelson@gmail.com. GitHub and LinkedIn links are in the header and footer
of every page.

# What can this assistant answer?

Only what is published on this site: Nelson's background and the write-ups for his
projects. It does not have access to anything private, and it will say so if you ask it
something it does not have material for.

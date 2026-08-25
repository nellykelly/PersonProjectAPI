# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary users are recruiters and hiring managers screening Nelson Koskela as a candidate.
They arrive with limited time and are trying to quickly gauge engineering ability and
credibility, not just read a resume in prose form.

## Product Purpose

A personal portfolio site for Nelson Koskela (Software Engineer II, JPMorgan Chase & Co.,
Corporate & Investment Banking) that demonstrates real engineering work rather than
describing it. Success means a recruiter/hiring manager leaves convinced of genuine,
production-grade engineering skill — infrastructure, backend systems, and full working
projects, not just a list of claimed skills.

## Positioning

The projects are real, running systems with real infrastructure decisions behind them
(queues, workers, caching, rate limiting, live analytics on the site's own traffic) —
not portfolio-piece toy demos. A neighboring "list of projects with GitHub links"
portfolio could not truthfully claim the same operational depth (Docker Compose stack
with worker/postgres/redis/caddy, monitored, deployed, documented down to migration and
DNS/TLS gotchas).

## Operating Context

- Live at https://nelsonkoskela.dev, deployed via Docker Compose on a Hetzner VPS
  (web, worker, postgres, redis, caddy).
- Built with Flask 3 (application-factory + blueprints), Flask-SQLAlchemy,
  Flask-Limiter, Flask-SocketIO, RQ + Redis, Postgres/SQLite, vanilla JS + Chart.js
  (CDN) + Socket.IO client (CDN) on the frontend.
- Multi-page Jinja templates (`base.html` + per-blueprint templates) rebuilt from a
  single-page HTML5 UP "Dimension" template into the current site structure.
- A separate `/documentation` section is a long-form engineering reference with its
  own stylesheet (intentionally not part of the main site's visual theme) — out of
  scope for the redesign unless the user says otherwise.

## Capabilities and Constraints

- Sections: Home, About (bio + resume download), Projects (landing page + 5 active
  projects + earlier-projects archive), Contact, Documentation (+ password-gated
  interview-prep subsection, never linked/indexed).
- Active projects: Company Scorer (SEC EDGAR + market data valuation scoring),
  Pipeline World (live queued CI/CD-style pipeline visualization), SRE Infra Layer
  (Redis queue/cache/rate-limit dashboard), Site Traffic Analytics (this app's own
  request analytics), Timed-Squares (HTML5 Canvas survival game with leaderboard).
  Trading Simulator exists and works but is deliberately unlisted/on hold — do not
  surface it in navigation or the redesign's project set.
- Every page must carry GitHub/LinkedIn/resume/email links (header and footer) and
  a site-wide disclaimer that nothing on the site is financial advice (Trading
  Simulator and Company Scorer specifically use simulated/delayed data).
- Stack is not up for redecision here — this is a visual redesign of an existing
  Flask/Jinja app, not a rebuild on a new framework.

## Brand Commitments

- Name: Nelson Koskela. Links to GitHub (nellykelly), LinkedIn, email
  (koskela.nelson@gmail.com) are binding, standing commitments on every page.
- Existing project icons are hand-drawn SVGs (`app/static/assets/img/icons/`), not
  stock art — an existing anti-generic commitment worth preserving or replacing in
  kind (hand-crafted, not stock), not defaulting to generic iconography.
- The current visual identity (HTML5 UP "Dimension" dark template, CCA 3.0) is
  explicitly the thing being replaced — it is evidence and anti-reference for the
  redesign, not an identity to preserve. Goal: stop reading as a templated/generic
  (or "AI-generated-looking") portfolio.

## Evidence on Hand

- Real, running projects (see Capabilities and Constraints) — not fabricated case
  studies or testimonials. No customer testimonials, benchmarks, or pricing exist
  and none should be invented.
- Resume is a real downloadable asset (About page).
- `docs/build-spec.md` and `docs/build-spec-pipeline-world.md` hold the original
  build specs if deeper project detail is needed.

## Product Principles

1. Prove, don't claim — real running infrastructure and real data over descriptive
   marketing copy.
2. Credibility reads through craft — a recruiter's first visual impression should
   support, not undercut, the engineering credibility the projects demonstrate.
3. Hand-crafted specificity over generic/templated polish — avoid stock-feeling
   patterns (icons, layouts, copy) that read as interchangeable with any other
   portfolio or as AI-generated.
4. Honesty in constraints — financial-advice disclaimers and the Trading Simulator's
   unlisted status are deliberate and must survive any redesign.
5. Respect the audience's time — a recruiter/hiring manager scanning quickly should
   find the strongest proof of skill fast, not have to dig for it.

## Stack

Existing codebase: Flask 3 + Jinja templates, vanilla JS + Chart.js + Socket.IO
(CDN), no frontend build step/framework. Redesign works within this stack.

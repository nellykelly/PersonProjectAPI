---
title: "Project: SRE Infra Layer"
kind: project
---

# What it is

The Redis-backed queueing, caching and rate-limiting infrastructure underneath Pipeline
World, written up and dashboarded as its own project at /projects/sre-infra because it
demonstrates a distinct skill set — async processing, cache invalidation, abuse
protection under load — worth speaking to separately from Pipeline World's
application-engineering pitch.

# Queue

Every character-join event is enqueued rather than processed synchronously: the HTTP
request returns immediately while a worker runs the real pipeline in the background. The
same worker pool now also runs a second kind of job, the Trading Simulator's risk
pricing, on two named queues over one Redis connection — one process, two job types,
rather than a second bespoke worker.

There are three environments with three behaviours: real Redis with a separate worker
process in Docker; an in-memory `fakeredis` instance with a worker thread inside the
Flask process for local development without Docker (still genuinely asynchronous); and
synchronous execution under the test suite for determinism. A Windows-specific finding
along the way: RQ's default worker forks a child process per job, which does not exist on
Windows, so the project uses `SimpleWorker` (no forking) consistently in both the
in-process fallback and the real Docker worker — one tested code path, not two.

# Cache

The live world state is cached with the cache-aside pattern: a read tries Redis first and
only queries Postgres on a miss, repopulating the cache afterwards. The cache is
explicitly invalidated the moment a character clears the Deploy stage — not left to
expire on a timer — so a newly-live character appears immediately. Both a TTL safety net
and explicit invalidation are present; that combination is the actual pattern.

# Rate limiting

The public, anonymous character-submission endpoint is rate-limited per IP with
Flask-Limiter backed by Redis, so the limit survives a container restart and holds
across multiple worker processes rather than living in one process's memory.

# What it demonstrates

Why synchronous processing does not scale for bursty work, a defensible caching strategy
named explicitly, and load and abuse handling — the competencies an infra or SRE
interview screens for.

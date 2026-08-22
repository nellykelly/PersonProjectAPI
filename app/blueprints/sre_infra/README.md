# <img src="../../static/assets/img/icons/sre-infra.svg" width="32" height="32" alt=""> SRE Infra Layer

**Route:** `/projects/sre-infra`

The Redis-backed queueing, caching, and rate-limiting infrastructure underneath
[Pipeline World](../pipeline_world/README.md) -- written up and dashboarded as its own
project because it demonstrates a distinct skill set (async processing, cache
invalidation, abuse protection under load) worth speaking to separately from Pipeline
World's application-engineering pitch.

## Queue (RQ + Redis)

Every character-join event is enqueued rather than processed synchronously in the
request/response cycle: the HTTP request returns immediately, while a worker runs the
real pipeline in the background (`app/services/pipeline.py`). This is the standard answer
to "why doesn't synchronous processing scale for anything resource-intensive or bursty."

Three environments, three behaviors (see `app/services/queue.py`'s module docstring):

- **Docker / real Redis**: a genuine separate `worker.py` process (its own container)
  consumes the queue.
- **Local dev without Docker**: falls back to an in-memory `fakeredis` instance with a
  worker running in a background thread of the same Flask process -- so the async
  behavior still demos (the request still returns immediately, stages still progress
  visibly over real time) without needing a real Redis server.
- **Tests**: synchronous (`is_async=False`) for deterministic, fast tests.

One concrete, Windows-specific finding from building this: RQ's default `Worker` forks a
child process per job (`os.fork()`), which doesn't exist on Windows at all, and separately
installs SIGINT/SIGTERM handlers, which only works on a process's *main* thread. `rq`'s
`SimpleWorker` (no forking) plus skipping signal installation resolves both -- used
consistently in both the in-process fallback and the real `worker.py`, so it's the same
code path either way, not two untested variants.

## Cache (Redis, cache-aside)

The live world state (which characters are currently spawned, and where) is cached with
the **cache-aside pattern**: a read tries Redis first, and only queries Postgres on a
miss, repopulating the cache afterward. The cache is explicitly invalidated the moment a
character clears the Deploy stage -- not left to expire on a timer -- so a newly-live
character shows up immediately instead of waiting out a stale TTL. Hit/miss counters are
tracked in Redis too, shown live on this page.

## Rate limiting (Flask-Limiter + Redis)

The character-submission endpoint is public and anonymous, so it needs the same abuse
protection as the Trading Simulator's position-opening endpoint: rate-limited per IP,
backed by Redis (`RATELIMIT_STORAGE_URI`) in the Docker deployment so the limit holds
across every worker process, not just one -- a plain `memory://` backend wouldn't share
state across gunicorn's multiple worker processes.

## Try it

Open [Pipeline World](../pipeline_world/README.md) and submit a character, then come back
here (or refresh) -- queue/cache stats update to reflect what just happened.

## Key files

- `app/blueprints/sre_infra/routes.py`
- `app/services/queue.py`, `world_cache.py`
- `worker.py` (repo root) -- the real `rq worker` entrypoint for Docker
- `docker-compose.yml` -- `redis`, `postgres`, and `worker` services

## Tests

`tests/test_queue.py`, `tests/test_world_cache.py`, `tests/test_sre_infra.py` -- all
against `fakeredis` (no live Redis needed); the in-process worker thread's actual
async behavior was verified manually end-to-end during development (enqueue returns
immediately, status progresses over real wall-clock time to `live`), since asserting on
background-thread timing in an automated test would be flaky.

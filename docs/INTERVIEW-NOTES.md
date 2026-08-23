# Interview notes: what's actually worth talking about

Internal reference, not linked from the site itself. Trimmed to the two things worth
walking an interviewer through -- a substantive system-design story, not a list of small
bug fixes.

---

## 1. How Pipeline World actually works, end to end

The pitch: a visitor submits a "character" (a name, a pick from a fixed appearance list,
and free-text answers to 4 fixed icebreaker questions -- no code, ever) and watches it
move through a real, queued CI/CD-shaped pipeline before it's allowed to exist in a live,
shared world. Worth being able to walk through this end to end, not just name-drop "there's
a pipeline":

1. **Join request lands, gets a light upfront check, and is enqueued -- not processed
   inline.** `POST /projects/pipeline-world/join` runs `validators.validate_join_request`
   for immediate UX feedback (format, injection pattern, profanity -- deliberately *not*
   full-name uniqueness, see step 3), creates a `Character` row with `status="pending"`,
   and enqueues an RQ job onto the `pipeline_world` Redis queue. The HTTP response comes
   back immediately; nothing about the actual pipeline has run yet.

2. **A worker -- a separate process/container from the web server -- picks up the job and
   runs the real 7-stage pipeline** (`app/services/pipeline.py`): Sanitize -> Security
   Scan -> Test: Uniqueness -> Test: Profanity -> Build -> Deploy -> Verify. Each stage:
   - emits a `start` event over Socket.IO carrying the actual pseudo-command it's about
     to run (e.g. the real shape of the SQL for the uniqueness check) -- this is what
     powers the live build-log feed every connected visitor sees, not just the submitter;
   - calls into the *same* validator functions covered by `tests/test_validators.py`, not
     a separate "pretend" check with different logic than what's actually tested;
   - writes a real `PipelineRun` row (stage, pass/fail, timestamps, a detail string) --
     the durable record `/pipeline-analytics` later queries, and also how a stage failure
     stays diagnosable after the fact instead of only in the moment it happened;
   - emits a `pass`/`fail` event over the same socket.

3. **A failure stops the pipeline right there, visibly, and the character never reaches
   Production Town.** Uniqueness in particular is checked *only* in this stage, not
   upfront at submission -- so two visitors submitting the same name back-to-back both
   get enqueued and genuinely race through the pipeline; whichever one loses the race
   fails at Test: Uniqueness with the real reason shown, instead of being silently turned
   away before it even started.

4. **Deploy is the one stage that actually changes anything -- Verify is a read-after-
   write check, not a rubber stamp.** Deploy assigns a world position and flips
   `status="live"`; Verify re-reads that same row from the database and confirms it
   landed the way Deploy thought it did, rather than assuming the write succeeded just
   because no exception was raised.

5. **The live world state is cache-aside, invalidated the instant Deploy lands, not on a
   timer.** `world_cache.py`: a read tries Redis first, falls back to Postgres on a miss
   and repopulates the cache; the cache is explicitly cleared the moment a character
   clears Deploy, so a brand-new character shows up in Production Town immediately
   instead of waiting out a TTL window. Production Town itself (`pipeline_town.js`) polls
   that cached state and renders it as an open field where characters wander and
   independently seek out the nearest free neighbor; two who close the distance hold a
   short scripted back-and-forth built from their (shared, fixed) icebreaker questions.

6. **`/pipeline-analytics` is the payoff -- real SQL over the `PipelineRun` history**, not
   a synthetic demo table: success rate over time, mean time between failures, slowest
   stage, a rolling 7-day pass rate (window functions / `DATE_TRUNC`, Postgres dialect --
   see `app/services/analytics.py`), each rendered with its actual query visible in a
   `<details>` block so it's checkable, not just claimed.

**One-sentence summary if asked to compress it:** input gets validated and enqueued, a
separate worker actually runs a 7-stage pipeline against it with each stage's result
persisted and pushed live over websockets, and only a stage that genuinely passes gets to
change anything -- which is also exactly what the analytics page is querying.

---

## 2. The Redis fallback (three-tier), and why it matters

`app/services/queue.py` picks its behavior based on environment, not a single hardcoded
assumption:

| Environment | Redis backend | Job execution | Why |
|---|---|---|---|
| Docker (`REDIS_URL` set) | real Redis | async, consumed by a separate `worker.py` process | the real deployment target |
| Local dev, no Docker | `fakeredis` (in-memory) | async, via a worker **thread** inside the same Flask process | still genuinely asynchronous (request returns immediately, stages progress over real wall-clock time) without needing a Redis install |
| Tests (`TESTING` config) | `fakeredis` | **synchronous** (`is_async=False`) | deterministic, fast, no sleeps/polling in test code |

**The interview story here isn't "I used fakeredis as a mock"** -- it's that the fallback
preserves the *actual property under test* (asynchronous processing) rather than
silently degrading to synchronous execution the moment Redis isn't available. A shortcut
version of this would just run the pipeline inline when there's no Redis; that would
demo fine but would be lying about the architecture. Verified this distinction live
during development: enqueued a job, confirmed the character was still `pending`
immediately after the HTTP response returned, then watched status progress
`validating -> testing -> building -> deploying -> live` over ~6 real seconds via a
background thread -- not a synchronous call that happened to look async.

**A concrete portability bug this surfaced:** RQ's default `Worker` class forks a child
process per job (`os.fork()`), which doesn't exist on Windows at all -- so naively
"just run an RQ worker" would have silently only worked in Docker/Linux and never
locally on the dev machine. Switched to `SimpleWorker` (no forking) everywhere,
including the real `worker.py` used by Docker -- one tested code path instead of two,
one of which would only ever run in production and never see a test.

A second wrinkle from the same fallback: running that worker in a background *thread*
(not process) meant RQ's attempt to install SIGINT/SIGTERM handlers blew up with
`signal only works in main thread of the main interpreter` -- Python's stdlib
restriction, not an RQ bug. Fixed with a one-method subclass override
(`_install_signal_handlers` -> no-op).

# Interview notes: what's actually worth talking about

Internal reference, not linked from the site itself. Sections 1-2 are what to actually
lead with if asked about this project -- substantive architecture walkthroughs, not a
list of small bug fixes. Everything else is kept below in an appendix: real, still
worth having written down somewhere, just not top-of-mind material.

---

## 1. How Pipeline World actually works, end to end

The pitch: a visitor submits a "character" (a name, a pick from a fixed appearance list,
and free-text answers to 4 fixed icebreaker questions -- no code, ever) and watches it
move through a real, queued CI/CD-shaped pipeline before it's allowed to exist in a live,
shared world. Worth being able to walk through this end to end, not just name-drop "there's
a pipeline":

1. **Join request lands and is enqueued unchecked -- nothing validates it first.**
   `POST /projects/pipeline-world/join` calls `validators.prepare_join_submission`, which
   only strips each field and truncates it to its column width; it never rejects on
   content. The `Character` row is stored exactly as typed with `status="pending"`, an RQ
   job goes onto the `pipeline_world` Redis queue, and the HTTP response returns
   immediately with no pipeline work done yet.

   This is worth calling out as a *design correction*, because the first version did the
   obvious thing and validated at the route: same charset, injection, and profanity
   checks, run upfront so an obviously-bad submission never became a job. It looked
   sensible and it quietly broke the product -- a visitor who typed a blocked word got a
   red message under the form and **no pipeline run at all**, so the single most
   interesting thing the project does was the one thing nobody could ever watch happen.
   Now every rule lives in exactly one place, the stage that owns it, and bad input
   produces a run that visibly fails. The general lesson: "fail fast" and "fail visibly"
   are not the same goal, and validating early can delete the observability that was the
   whole point of building the thing.

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

3. **A failure stops the pipeline right there, and the character never reaches Production
   Town.** No later stage runs, so no later `PipelineRun` row exists -- the tracker table
   renders those as "not reached", and a PASS can never appear to the right of a FAIL.
   The character is left `failed` with the reason attached. Uniqueness is a good example
   of why the checks belong here rather than at submission: two visitors submitting the
   same name back-to-back both get enqueued and genuinely race, and whichever loses fails
   at Test: Uniqueness with the real reason shown, rather than being turned away before
   starting.

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

---
---

# Appendix: everything else (kept for reference, not top-of-mind for an interview)

Real decisions and real bugs, all still true -- just the kind of thing that comes up if
asked a follow-up question, not what to lead with. Sections 1-2 above are what to lead
with.

## A. Real bugs found by actually running the thing (not just reading the diff)

Every one of these was caught by exercising the feature live in a browser or via a
standalone verification script -- not by code review alone.

- **Self-collision in Pipeline World's Validate stage.** `check_full_name_collision`
  queried for an existing character with the same name -- but by the time the pipeline's
  own Validate stage re-checks this rule, the character *is already the row in the
  database being checked against*. Every single pipeline run failed at Validate,
  100% of the time, immediately. Fixed with an `exclude_character_id` param. This is a
  classic "the check is correct in isolation, wrong in context" bug -- the same function
  is called from two places (pre-insert at the join route, post-insert inside the
  pipeline) with different correctness requirements.

- **`current_app` resolved inside a lazily-executed generator (found twice, same root
  cause, two different features).** Flask pops the request context as soon as a view
  function *returns* -- for a normal view that's fine, but an SSE/streaming response
  returns a `Response(generator(), ...)` immediately, and the generator body only
  executes later, when the WSGI server iterates it, by which point the context is gone.
  Any `current_app` access inside that generator raises
  `RuntimeError: Working outside of application context`. Hit this in both the Trading
  Simulator's live watchlist stream and would have hit it again in Pipeline World's
  Socket.IO wiring had the pattern not already been learned -- the fix is always the
  same: capture the real app object (`current_app._get_current_object()`) *before*
  defining the generator, not inside it.

- **A pseudo-URL scheme broke host-grouping in the Network Sniffer.** `market_data.py`
  logs yfinance calls as e.g. `yfinance://AAPL/info` (yfinance manages its own HTTP
  client, so there's no real URL to log). The "group outbound calls by host" logic did a
  naive `target.split("/")[2]`, which is correct for real `https://host/path` URLs but
  reads the *ticker* as the host for this synthetic scheme -- so the dashboard grouped
  calls by ticker symbol instead of by service. Fixed by branching on whether the target
  is a real `http(s)://` URL (extract the host) or something else (use the scheme name
  instead) -- and had to fix it in *two* places at the time, since the client-side JS
  recomputed the same stats independently for live SSE updates rather than re-fetching
  from the server. (That duplication is gone now that the page polls a server-computed
  analytics endpoint instead of streaming raw entries -- one fewer place this exact class
  of bug could recur, a side benefit of the later rebuild, not the reason for it.)

- **A CSS reset collision, not a logic bug.** The site's dark theme borrows a global
  `button { height: 2.75rem; line-height: 2.75rem; white-space: nowrap; ... }` rule from
  the underlying HTML5UP template, tuned for a single line of button label text. The
  watchlist's ticker tiles are `<button>` elements holding three stacked lines
  (ticker/price/timestamp) -- the fixed short height meant content overflowed the
  visible border box, and because grid rows only size to their *declared* height (not
  overflowing content), the overflow visually bled into the next row down, making tiles
  look staggered/offset from their borders. Caught from a user-provided screenshot, not
  automated tests -- a good example of why "the tests pass" isn't the same as "it looks
  right," and why you still need to actually look at the rendered page.

- **`form.action` is never falsy.** `fetch(form.action || "/some/path")` looks like a
  reasonable fallback pattern, but an `<form>` with no `action` attribute still reports
  the *current page's URL* for `.action` (a DOM quirk, not a bug in the browser) -- so
  the `||` fallback never triggers, and the form silently POSTs to the wrong endpoint.
  Caught because the join form's success message never appeared during manual testing;
  root-caused by checking `read_network_requests` and seeing the POST hit the page URL
  instead of `/join`.

- **A narrow `except` clause let a crashed pipeline stage leave the world in an
  inconsistent state, live, in production.** `pipeline.py`'s `_run_stage` only caught
  `validators.ValidationError` around each stage's check function -- reasonable for the
  *expected* failure modes (a duplicate name, a blocked word), but a stage can fail for
  reasons that have nothing to do with the input being validated: here, almost certainly
  the `worker` container getting killed mid-job by one of this same session's own
  redeploys. Deploy had already written the character's `status = "live"`, but Verify --
  the very next stage, whose only job is a read-after-write sanity check -- never got the
  chance to run or fail cleanly, leaving a character genuinely live in Production Town
  with no Verify `PipelineRun` row at all: not failed, not passed, just missing, which is
  a state the UI had no way to render as an error because nothing had actually raised one
  it was watching for. Found from a user-provided screenshot of the Pipeline Runs table
  showing a blank Verify cell, not a crash log. Fixed with a broad `except Exception`
  fallback around the same `_fail()` un-live path, so *any* unhandled exception at *any*
  stage now fails loudly and visibly instead of leaving a half-finished row -- and the one
  real character already affected was remediated directly in the production database
  (after confirming the deploy genuinely had succeeded, an honest backfilled Verify
  *pass*, not a punitive fail, since the character's data was actually fine -- only the
  bookkeeping about it was missing). The general lesson: a `try/except` scoped to the
  errors you expect is a correctness statement about the happy path, not a safety net --
  the safety net is the catch-all at the orchestration boundary, and skipping it because
  "that shouldn't happen" is exactly the case it exists for.

- **Unhandled exceptions escaping a service boundary.** `edgar.py`'s HTTP calls used
  `requests` directly; any `requests.exceptions.*` (a timeout, a DNS failure, and in
  this specific sandbox, a local antivirus's TLS interception rejecting the connection)
  propagated straight past the route's `except (MarketDataError, EdgarError)` handler
  and 500'd. The single-ticker scoring route already degraded gracefully because
  `market_data.py`'s retry wrapper *does* catch broad exceptions and re-raise as
  `MarketDataError`; `edgar.py` didn't have the equivalent wrapper. Fixed by catching
  broadly at the HTTP call site and re-raising as `EdgarError` uniformly -- the lesson:
  a "graceful degradation" pattern has to be applied at every integration point
  individually, copying it to one call site doesn't protect the others.

## B. Architecture decisions worth explaining the *why* behind

- **SSE vs WebSockets, used for different reasons in the same codebase.** The Network
  Sniffer and Trading Simulator's watchlist use Server-Sent Events (one-way server ->
  client push, plain HTTP, no extra client library, reconnects automatically). Pipeline
  World uses Socket.IO/WebSockets instead -- chosen because the spec called for it
  explicitly, and because a richer bidirectional protocol is a more defensible choice
  once there's real interactive potential (future client-originated events), even though
  the current version is also push-only in practice. "I used the simpler tool (SSE)
  everywhere it was sufficient, and the heavier one only where the spec asked for it or
  the interaction model justified it" -- not "I used WebSockets because they're more
  impressive."

- **Cache-aside with explicit invalidation, not just a TTL.** Pipeline World's live
  world state is cached in Redis, but the cache isn't just "expire after N seconds" --
  it's explicitly invalidated the instant a character clears Deploy. TTL-only caching
  would mean a newly-spawned character might not appear for up to the TTL window: fine
  for some use cases, wrong here, where "does this feel live" is the actual product
  requirement. Both mechanisms are present (TTL as a safety net if invalidation is ever
  missed, explicit invalidation for the common case) -- that combination, not either
  alone, is the actual cache-aside pattern.

- **Reusing tested validation code as the pipeline's "Test stage," instead of literally
  shelling out to `pytest` per run.** The spec asked for the pipeline's Test stage to run
  "an actual small test suite." The literal interpretation (`pytest.main()` invoked
  per character, inside a worker loop) is slow, fragile, and awkward to get data into
  test functions designed to be collected by pytest's discovery mechanism. The choice
  made instead: `validators.py`'s functions are covered by real, non-trivial
  `pytest` tests (`tests/test_validators.py`) *and* those exact same functions are what
  the live pipeline calls during its Test stage. It's honestly a documented tradeoff, not
  a free lunch -- worth being upfront about the reasoning if asked, rather than
  overclaiming "it's literally pytest.main() under the hood."

- **Point-in-time correctness in the Company Scorer backtest, to avoid look-ahead bias.** Scoring a
  company "as of 1 year ago" needs fundamentals that were *actually public* a year ago,
  not fundamentals as currently known (which might include restatements or data that
  wasn't filed yet at that date). `edgar.py` filters SEC filings by *filed* date, not
  *period-end* date, specifically to avoid this.

- **Configurable, renormalizing category weights in the Company Scorer.** When a company is
  missing data for a metric (a common real-world XBRL taxonomy inconsistency), that
  metric is dropped and the remaining weights renormalize over what's actually
  available, rather than either erroring out or (worse) silently treating the missing
  value as zero, which would unfairly tank the score. Weights themselves are config, not
  hardcoded constants -- "equal-weight by default, tunable later" was an explicit
  requirement, not a nice-to-have.

- **Ticker whitelisting + parametrized queries, not either alone, for injection
  defense.** Both the Trading Simulator and Company Scorer validate ticker symbols against a
  fixed whitelist before they ever reach `yfinance`/SEC EDGAR calls. But the *actual*
  SQL/injection defense is that every database query is parametrized (SQLAlchemy bound
  parameters) -- the whitelist is defense-in-depth on top of that, not a substitute for
  it. Pipeline World's name validation follows the same logic: a character whitelist
  (letters/spaces/hyphens/apostrophes) that *also* happens to reject HTML/script tags
  and SQL metacharacters, layered on top of parametrized queries, not instead of them.
  "How do you prevent SQL injection" -- the honest answer is "parametrization; the input
  whitelist is a second layer, not the mechanism."

- **A hashed profanity blocklist, not a plaintext wordlist.** Rather than committing a
  literal list of blocked words to a public repository, the blocklist stores SHA-256
  hashes and hashes each candidate word the same way for comparison -- a real,
  functioning check, without the objectionable words sitting in plaintext in git
  history forever. (The list itself shipped with only three placeholder words for a
  while before anyone swapped in a real one -- see section G below.)

- **SQLite everywhere except where it genuinely can't do the job.** Every model in the
  app uses portable column types and works against SQLite -- except Pipeline World's
  analytics queries, which are hand-written Postgres-dialect SQL (`DATE_TRUNC`, window
  function frames) because the whole point of that page is demonstrating real,
  non-trivial SQL, and faking window functions with Python loops would undercut exactly
  the thing being showcased.

## C. Legal/ethical scoping decisions

- **Site Traffic Analytics (formerly "Network Sniffer") only logs the app's own
  traffic**, never a visitor's browsing -- capturing arbitrary visitor traffic is
  treated as wiretapping in most jurisdictions regardless of intent, and would violate
  almost any host's ToS. Scoped to inbound requests to the app's own routes and outbound
  calls the app itself makes. The page was later rebuilt from a raw live log into an
  aggregate analytics board (volume over time, latency percentiles, error rate), but the
  scoping rule underneath -- what's captured at all -- didn't change.

- **Pipeline World never executes visitor-submitted code, by construction.** The entire
  input surface is a name and a pick from a fixed list -- there's no code-execution
  attack surface to defend because there's no code path that treats visitor input as
  anything other than a string to validate and store. This is a stronger guarantee than
  "we sandboxed the code execution" -- there simply isn't any.

## D. Security incident handling (real, not hypothetical)

While rebuilding the site, found two real Google OAuth secrets (`credentials (2).json`,
a client secret; `token.pickle`, a live refresh token) committed in the legacy repo's
history -- confirmed present in `master`'s current tree via the GitHub API. Handled in
stages, each with the right amount of caution for its blast radius:

1. Removed from the current tree with a normal commit (non-destructive, safe to do
   immediately).
2. Once asked to fully clean history: cloned fresh into an isolated scratch directory,
   ran `git-filter-repo` to strip the files from every commit -- and found a *second*,
   older exposure the first pass missed (`courses/credentials (2).json`, from before the
   code was reorganized under `app/`), by diffing which paths had ever existed across
   all of history rather than assuming the current path was the only one.
3. Verified the purge two ways before pushing: no matching *paths* in any commit, and
   (more rigorously) `git grep` across every blob in history for the actual client ID
   string, to catch the case where the file might have been renamed rather than just
   deleted-and-recreated at a new path.
4. Force-pushed the rewritten history only after that verification, and explicitly
   flagged that the exposed credentials should be rotated regardless -- deleting them
   from git doesn't undo whatever already saw them while they were public.

The progression worth remembering: "fix the symptom safely" -> "fully remediate, but
verify before doing anything destructive" -> "the fix doesn't replace rotating the
actual secret" (which is still the one open item -- see `docs/SECURITY-NOTE.md`).

## E. The position-level risk rework: bugs and decisions

Turning the Trading Simulator's risk engine from "price one leg" into "price a whole
position (or the whole book), all in one job, on a separate worker" surfaced several bugs
that were genuinely instructive, not cosmetic.

- **A silently-corrupting bug that never got the chance to exist.** `submit_risk_request`
  used to take a leg id as its first *positional* argument. Adding a `strategy_id` keyword
  meant a stale call site written as `submit_risk_request(42)` would now be silently
  reinterpreted as *pricing position 42* instead of *leg 42* -- wrong answer, no error,
  nothing to notice. Made every argument keyword-only instead, which turned exactly that
  mistake into an immediate `TypeError` -- and it did: the refactor's own test suite had
  11 old call sites written the old way, and every one of them failed loudly and
  specifically instead of quietly pricing the wrong thing. The lesson isn't "keyword-only
  is good style" -- it's that a signature change that silently changes an existing
  positional argument's *meaning* is exactly the situation worth spending a few extra
  characters to make unrepresentable.

- **NaN is not valid JSON, and Python will happily lie to you about that.** `yfinance`
  returns `NaN` for missing option-chain quotes (thin strikes, no recent trade). Python's
  own `json.loads` accepts a bare `NaN` token as an extension to the spec -- so testing
  the endpoint with `curl | python -m json.tool` looked completely fine. A real browser's
  `JSON.parse` follows the actual JSON spec, where `NaN` is a syntax error, and rejected
  the response outright; the `.catch()` around it reported a generic "could not load
  chain" with no indication why. Two tools disagreeing about what "valid JSON" means is
  the whole story here -- verifying with the *same* JSON parser the browser uses (a
  strict `parse_constant` that rejects `NaN`/`Infinity`) is what actually caught it, and
  that strict-parser test is checked into `tests/test_market_data_json_safety.py` along
  with a test proving the strict parser itself would reject `NaN` -- otherwise the guard
  could quietly stop testing anything.

- **Exceptions don't cross a process boundary -- the row does.** Once risk pricing moved
  onto a separate `worker` container, `submit_risk_request` (still in the web process)
  can't just wrap the pricing call in `try/except` anymore -- the code that might raise
  `MarketDataError` now runs somewhere else entirely. The fix: the worker job catches its
  own exceptions and writes a plain-text reason onto the `RiskRequest` row
  (`error = "market_data: ..."`), and the waiting web-process code re-derives the *same*
  exception type from that string once the row stops reading `pending`, so every existing
  route's `except MarketDataError` handler keeps working without having to know pricing
  moved to a different process at all. Distributed systems don't get to "just raise"
  across a process boundary -- the shared state (here, the database row) has to carry
  both the result *and* the failure, because there's no shared call stack to unwind.

- **The NOT-NULL-on-a-populated-table migration trap, four times running.** Every
  Alembic autogenerate for a new `nullable=False` column against a table that already has
  rows generates SQL that fails immediately -- Postgres backfills existing rows with
  `NULL` before it ever checks the constraint, and a bare `NULL` obviously fails `NOT
  NULL`. Hit this for `instruments.instrument_type`'s width fix, for `risk_requests.model_key`,
  for `risk_requests.strategy_id`/`scope`, and for `instruments.code` -- each time the
  fix is the same shape: add the column nullable, backfill every existing row with a real
  (not placeholder) value in the same migration, *then* tighten to `NOT NULL`. Two
  flavors of backfill: a single constant works with `server_default` (e.g. every existing
  risk request defaulting to `model_key = 'trader_granular'`, since that was the only
  model that existed before request-level model selection), while a value that varies
  per row (each instrument's own OCC code) needs an actual Python or SQL loop computing
  the real value per row -- a single `server_default` can't do that.

- **A regression guard catching a bug it wasn't written for.** `tests/test_models_column_widths.py`
  was built earlier to stop a *different* VARCHAR-too-narrow bug from recurring, and it
  audits every `String` column's declared width against the real values the app can
  write. Adding `Instrument.code` (an OCC symbol) tripped it immediately: the column was
  declared `String(24)`, but the actual worst case -- a 10-character ticker (the width
  `underlying_ticker` itself already allows) + 6-digit expiry + C/P + 8-digit strike -- is
  25 characters, one over. That's the guard doing exactly its job on a bug that didn't
  exist when it was written, which is the actual point of a coverage-style regression
  test over a narrow, bug-specific one.

- **A tool silently rewriting the very path it was told to check.** Debugging why a
  freshly-generated migration file "wasn't there" inside the `worker`/`web` containers led
  to `docker compose exec web ls /app/migrations/versions` returning `No such file or
  directory: D:/Git/app/migrations/versions` -- Git Bash's MSYS layer auto-converts
  Unix-looking absolute paths in command arguments to Windows paths *before* Docker ever
  sees them, so `/app/...` silently became a local Windows path that obviously doesn't
  exist inside a Linux container. `MSYS_NO_PATHCONV=1` (or a `//app/...` double-slash
  escape) disables that translation for the one command that needs a literal
  container-side path. A good reminder that "the file doesn't exist" and "the tool never
  saw the path I typed" are different bugs that look identical from the error message.

- **Rebuilt the image, generated the migration, forgot to rebuild again.** The actual
  root cause behind the above investigation, once the path issue was ruled out: a
  migration was correctly generated against a freshly-built image (so autogenerate could
  see the new model columns), copied out to the host with `docker cp`... and then the
  *next* `docker compose up` started containers from the image built *before* that file
  existed on the host, because the image bakes in whatever was in the build context at
  build time. The established pattern (build -> generate -> copy out -> **rebuild again**
  -> apply) has a rebuild on both sides of the copy for exactly this reason; skipping the
  second one produces a container that looks like it ran `flask db upgrade` successfully
  while silently sitting one migration behind.

- **A correction worth taking seriously even after independent research initially seemed
  to contradict it.** Told that calling a lone, non-spread trade a "leg" was wrong,
  the first instinct was to check the actual definition -- and industry sources do
  confirm "a single-leg strategy" is real, standard terminology, which read like the
  complaint might be unfounded. The real distinction, once asked directly where
  specifically it looked wrong, turned out to be narrower and correct: a *leg* still
  requires an actual multi-part structure to be part of, and a standalone trade with no
  siblings has no such structure, so calling it "1 leg" (a UI stat tile literally reading
  "Legs priced: 1") is imprecise in a way that "a single-leg *strategy*" as a category
  name is not. The fix was scoped to exactly that: "leg" language only appears once a
  request or a position actually spans more than one instrument; a lone trade says
  "single trade" and skips the aggregate sections entirely rather than trivially
  restating its own single result under a "position totals" heading. Research that seems
  to vindicate your first instinct doesn't mean the correction was wrong -- it can mean
  the real distinction is narrower than either side's first framing.

## F. Timed-Squares: catching state-machine bugs by actually driving the state machine

Timed-Squares (`app/static/js/timed_squares.js`) has no Python test coverage -- it's
client-side JS with no test runner wired into this repo -- so verifying its turn
resolution, telegraphing, and each obstacle's movement math meant instantiating the
actual game engine in a real browser and driving it directly (`tryMovePlayer` calls, then
real dispatched `keydown` events) rather than trusting the code by inspection.

That's what it was for: a first pass at the manual verification script tried to check the
bouncer's edge-reversal by handing it an already-invalid telegraphed move and asserting
it got corrected -- which failed, because that's not what the code does or should do. The
real invariant is *decided at telegraph time*: an obstacle's `nextMove` is only ever
computed by its own decider function (which checks whether the *next* step would leave
the board and flips direction pre-emptively if so), never hand-set to something the
decider never would have produced. Once the test was corrected to drive the obstacle
through a real spawn-shaped path -- give it a valid first move, let the engine execute
it, then inspect what it telegraphed *next* -- the actual sequence (move to the second-
to-last cell, telegraph flips to reversed, next move executes the reversal) checked out
exactly as designed. Worth remembering for any state machine: a verification script that
skips the state transition and pokes at intermediate state directly can produce a
failure that indicts the harness, not the code -- rerunning it through the real
transition path is what tells you which one is actually wrong.

## G. Taking it to production: real hosting, real bugs found only by shipping it

The site is live at `nelsonkoskela.dev` on a Hetzner CX22 VPS -- Docker Compose running
`web` (gunicorn), `worker` (RQ), `postgres`, `redis`, and `caddy` (reverse proxy + HTTPS)
as five containers on one small box. A few things only became visible once the app was
actually serving real traffic to the real internet, not `localhost`:

- **A single-process, 8-thread gunicorn command was quietly bottlenecking every static
  asset request through the same pool a live SSE connection can pin.** `Dockerfile` runs
  `gunicorn -w 1 --worker-class gthread --threads 8` -- one process, deliberately, because
  Flask-SocketIO's `threading` async_mode keeps a client's session in that one process's
  memory, and a second gunicorn *process* would round-robin requests for the same
  Socket.IO session across processes that don't share it. That's a correct, necessary
  constraint for Pipeline World's live updates -- but the `Caddyfile` was proxying
  *everything* to that same 8-thread pool, including the ~15-20 static CSS/JS/font/icon
  requests one page load fires off, all competing with any other visitor's already-open
  SSE stream (the live watchlist, the network sniffer) permanently occupying a thread for
  as long as that tab stays open. Reported by the actual user as "the landing page holds
  for ~4 seconds before I can scroll" -- reproducible every load, not just cold-cache.
  Fixed by having Caddy's own `file_server` answer `/static/*` directly off a read-only
  bind mount of `app/static`, bypassing the app process (and its thread limit) entirely
  for anything that's just a file on disk. A resource limit that's *correct* for the
  reason it exists (Socket.IO session affinity) can still be a bug everywhere else that
  same pool gets used for unrelated traffic -- the fix isn't "raise the limit," it's
  "stop routing traffic through it that never needed to be there."

- **A defense-in-depth blocklist that was still just three placeholder words in
  production.** `validators.py`'s hashed profanity blocklist (see section B) was seeded
  with `("damn", "hell", "crap")` -- explicitly labeled placeholders during development,
  never swapped for a real wordlist before the site went live. A visitor named a
  character "Fuck Fuck," it sailed through Test: Profanity, and went live in Production
  Town. The mechanism itself was never the bug (the hashing, the stage wiring, the
  hard-fail-and-never-reach-Production-Town path all worked exactly as designed and
  tested) -- the *data* backing it was a demo, not a real check, and nothing about the
  passing test suite could have caught that distinction, because the tests only ever
  asserted the mechanism worked against whatever list was configured. Fixed by replacing
  the placeholder tuple with an actual wordlist, then retroactively un-living the one
  character that had gotten through: set `status = "failed"`, wrote a real `PipelineRun`
  row recording *why* (`stage="test_profanity"`, a fail, with a reason), and invalidated
  the world cache -- the same shape of fix as the crashed-pipeline-stage incident in
  section A (production data that's already wrong needs the affected row corrected
  directly, not just the code fixed for next time). "Defense-in-depth" and "tested" don't
  mean much if the actual data behind the check was never filled in for real -- a
  mechanism test passing is not the same claim as the check being effective.

- **A stat-tile overflow bug came back in a different shape after the first fix.**
  Originally "long numbers wrap mid-digit" -- fixed by switching
  `overflow-wrap: anywhere` to `normal` plus a `$1M` abbreviation threshold. That fix was
  incomplete: a 6-figure *negative* PnL (`$-143,436`, 9 characters) is still wide enough
  to overflow the tile once wrapping is disabled -- except this time, instead of visibly
  breaking mid-digit, the text silently spilled past the tile's edge and got hidden under
  whatever opaque tile sat next to it in the grid, which reads as "the number got cut
  off" rather than "the number is too long," a materially different-looking bug from the
  same root cause. The fix wasn't a new CSS rule -- it was extending the *existing*
  abbreviate-before-it's-too-long strategy one tier lower (a `$100K` cutoff, not just
  `$1M`), with the same rounding-boundary trap the first fix already knew about
  (`999,999.99` rounds to `$1.00M`) now needing a matching guard one tier down (a K-tier
  value that would itself round up to `1,000.0` needs to fall through to M instead of
  rendering the confusing `"$1,000.0K"`). The first fix's test suite passed the whole
  time, because it tested the exact values it was written against, not the wider class of
  value ("a value wide enough to overflow *at any tier*") the bug actually generalizes to.

- **Removing obstacles fixed a social feature, not a physics one.** Production Town's
  characters wander and independently seek out the nearest free neighbor within a notice
  radius (`findSeekTarget`), then steer straight toward them (`steerToward` +
  `applyMovement`) -- no pathfinding, just velocity nudged toward the target and a
  bounce-off-the-wall collision response. That's intentionally cheap: pathfinding
  (`findGridPath`, a real grid BFS) exists in this file, but only for the one long,
  rare walk from spawn to the park. The bug report was "none of the characters are ever
  talking" -- and the actual cause wasn't the seek logic, the notice radius, or the
  conversation-trigger distance at all: the map was a 4x3 grid of city blocks, each with
  1-2 hollow buildings, and straight-line steering with wall-bounce has no way to route
  around an obstacle -- two characters who'd genuinely noticed each other within range
  would just bounce against whatever wall sat between them indefinitely instead of ever
  closing the distance. The fix wasn't a smarter seek algorithm; it was replacing the
  city-block grid with one large, almost entirely obstacle-free open field (keeping a
  thin decorative row of landmark buildings along one edge, purely as backdrop, never in
  the path of anything). A "two agents aren't finding each other" bug can look like a
  search/AI problem when it's actually an environment-design problem -- the seek logic
  was correct the entire time; it just needed a world where correct-but-simple steering
  was actually sufficient.

- **Taking a site to a real domain surfaces a coupled-certificate trap.** Requesting one
  Let's Encrypt certificate covering both `nelsonkoskela.dev` and `www.nelsonkoskela.dev`
  in a single Caddy site block meant `www`'s DNS not being ready yet (still pointed at the
  registrar's default parking record) failed *that* domain's ACME validation and dragged
  the *entire* certificate order down to Let's Encrypt's untrusted staging CA as an
  automatic fallback -- so the bare domain, whose DNS was correct, still ended up serving
  an untrusted cert too, which looks like "HTTPS is just broken" rather than "one of two
  domains in this cert isn't ready." Diagnosed by checking the actual issuer
  (`openssl s_client | openssl x509 -noout -issuer`, looking for `O=Let's Encrypt` vs a
  "Fake LE" staging root) rather than trusting the browser's padlock icon alone. Fixed by
  dropping `www` from the Caddyfile until its own DNS record existed, letting the bare
  domain get a clean production cert on its own, then adding `www` back in once it had
  real DNS -- a multi-domain certificate is only as good as the least-ready domain in it.

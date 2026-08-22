# Interview notes: decisions, tradeoffs, and bugs worth talking about

Internal reference, not linked from the site itself -- a cheat sheet of the technical
decisions behind this repo worth bringing up in an interview, and the real bugs found by
actually running the code instead of just reading it. (Filename: you asked for
"explination" -- fixed the typo and picked a name that describes what's actually in here.)

---

## 1. The Redis fallback (three-tier), and why it matters

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

## 2. Real bugs found by actually running the thing (not just reading the diff)

Every one of these was caught by exercising the feature live in a browser or via a
standalone verification script -- not by code review alone. Worth mentioning in an
interview as *how* you catch bugs, not just that you can write code without them.

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
  instead) -- and had to fix it in *two* places, since the client-side JS recomputes the
  same stats independently for live SSE updates rather than re-fetching from the server.

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

---

## 3. Architecture decisions worth explaining the *why* behind

- **SSE vs WebSockets, used for different reasons in the same codebase.** The Network
  Sniffer and Trading Simulator's watchlist use Server-Sent Events (one-way server ->
  client push, plain HTTP, no extra client library, reconnects automatically). Pipeline
  World uses Socket.IO/WebSockets instead -- chosen because the spec called for it
  explicitly, and because a richer bidirectional protocol is a more defensible choice
  once there's real interactive potential (future client-originated events), even though
  the current version is also push-only in practice. Good interview answer: "I used the
  simpler tool (SSE) everywhere it was sufficient, and the heavier one only where the
  spec asked for it or the interaction model justified it" -- not "I used WebSockets
  because they're more impressive."

- **Cache-aside with explicit invalidation, not just a TTL.** Pipeline World's live
  world state is cached in Redis, but the cache isn't just "expire after N seconds" --
  it's explicitly invalidated the instant a character clears Deploy. TTL-only caching
  would mean a newly-spawned character might not appear for up to the TTL window: fine
  for some use cases, wrong here, where "does this feel live" is the actual product
  requirement. Both mechanisms are present (TTL as a safety net if invalidation is ever
  missed, explicit invalidation for the common case) -- that combination, not either
  alone, is the actual cache-aside pattern worth naming in an interview.

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

- **Point-in-time correctness in the QR backtest, to avoid look-ahead bias.** Scoring a
  company "as of 1 year ago" needs fundamentals that were *actually public* a year ago,
  not fundamentals as currently known (which might include restatements or data that
  wasn't filed yet at that date). `edgar.py` filters SEC filings by *filed* date, not
  *period-end* date, specifically to avoid this. This is the kind of detail that
  separates "I called an API" from "I understand why the naive version of this would be
  wrong" -- worth surfacing proactively.

- **Configurable, renormalizing category weights in the QR scorer.** When a company is
  missing data for a metric (a common real-world XBRL taxonomy inconsistency), that
  metric is dropped and the remaining weights renormalize over what's actually
  available, rather than either erroring out or (worse) silently treating the missing
  value as zero, which would unfairly tank the score. Weights themselves are config, not
  hardcoded constants -- "equal-weight by default, tunable later" was an explicit
  requirement, not a nice-to-have.

- **Ticker whitelisting + parametrized queries, not either alone, for injection
  defense.** Both the Trading Simulator and QR Scorer validate ticker symbols against a
  fixed whitelist before they ever reach `yfinance`/SEC EDGAR calls. But the *actual*
  SQL/injection defense is that every database query is parametrized (SQLAlchemy bound
  parameters) -- the whitelist is defense-in-depth on top of that, not a substitute for
  it. Pipeline World's name validation follows the same logic: a character whitelist
  (letters/spaces/hyphens/apostrophes) that *also* happens to reject HTML/script tags
  and SQL metacharacters, layered on top of parametrized queries, not instead of them.
  Good distinction to draw if asked "how do you prevent SQL injection" -- the honest
  answer is "parametrization; the input whitelist is a second layer, not the mechanism."

- **A hashed profanity blocklist, not a plaintext wordlist.** Rather than committing a
  literal list of blocked words to a public repository, the blocklist stores SHA-256
  hashes and hashes each candidate word the same way for comparison -- a real,
  functioning check, without the objectionable words sitting in plaintext in git
  history forever.

- **SQLite everywhere except where it genuinely can't do the job.** Every model in the
  app uses portable column types and works against SQLite -- except Pipeline World's
  analytics queries, which are hand-written Postgres-dialect SQL (`DATE_TRUNC`, window
  function frames) because the whole point of that page is demonstrating real,
  non-trivial SQL, and faking window functions with Python loops would undercut exactly
  the thing being showcased. Worth being able to explain *why* one piece of the stack
  needed a heavier dependency instead of just defaulting to "use Postgres for
  everything" or "avoid it everywhere."

---

## 4. Legal/ethical scoping decisions (good "how do you think about constraints" answers)

- **Network Sniffer only logs the app's own traffic**, never a visitor's browsing --
  capturing arbitrary visitor traffic is treated as wiretapping in most jurisdictions
  regardless of intent, and would violate almost any host's ToS. Scoped to inbound
  requests to the app's own routes and outbound calls the app itself makes.

- **Pipeline World never executes visitor-submitted code, by construction.** The entire
  input surface is a name and a pick from a fixed list -- there's no code-execution
  attack surface to defend because there's no code path that treats visitor input as
  anything other than a string to validate and store. This is a stronger guarantee than
  "we sandboxed the code execution" -- there simply isn't any.

---

## 5. Security incident handling (real, not hypothetical)

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

Good interview answer to "tell me about a time you found a security issue": the
progression from "fix the symptom safely" to "fully remediate, but verify before doing
anything destructive" to "the fix doesn't replace rotating the actual secret."

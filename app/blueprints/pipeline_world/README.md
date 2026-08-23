# <img src="../../static/assets/img/icons/pipeline-world.svg" width="32" height="32" alt=""> Pipeline World

**Routes:** `/projects/pipeline-world`, `/projects/pipeline-world/town`, `/projects/pipeline-world/pipeline-analytics`

A visitor submits a character (name, a pick from a fixed appearance list, and free-text
answers to 4 fixed icebreaker questions -- no code, ever) and watches it move through a
real, queued **7-stage pipeline** -- **Sanitize -> Security Scan -> Test: Uniqueness ->
Test: Profanity -> Build -> Deploy -> Verify** -- live, in a build-tracker table with a
live build-log feed, before it's written as live in Production Town.

> No code is ever submitted or executed. The inputs are a name, a choice from a fixed
> appearance list, and free-text answers to the same 4 fixed icebreaker questions every
> visitor answers -- see `app/services/validators.py`.

## Two pages, on purpose

Earlier version of this had one page with a small canvas cramming the pipeline
mechanics *and* the town into the same space -- it read as a random animation rather
than a real process, because neither the pipeline nor the town got enough room to
actually show what it was doing. Split into two:

- **`/projects/pipeline-world`** -- the join form, a live **build-log feed** (the actual
  pseudo-commands each stage runs, printed the instant it starts), and a **build-tracker
  table** (one row per character, one status cell per stage), modeled on an actual CI/CD
  run history view (GitHub Actions, CircleCI, etc.), not a game-world animation.
- **`/projects/pipeline-world/town`** -- Production Town on its own, large, dedicated
  canvas. Click any character to see their name, appearance, and all 4 icebreaker
  answers. Updates live the instant anyone's Deploy stage passes, from any tab. Two
  characters who wander close together strike up a conversation -- see below.

## The seven stages, and why each one is what it is

Each stage maps to one concrete concern -- not an arbitrary CI-flavored label -- and
each emits its actual pseudo-commands to the live build-log feed the instant it starts
(see `pipeline.py`'s `STAGE_COMMANDS`):

1. **Sanitize** -- input hygiene only: format, length, and charset for the name and each
   of the 4 icebreaker answers, plus a valid-option check for the appearance pick. No
   database reads.
2. **Security Scan** -- an explicit, independently-reported scan for HTML/script tags
   and SQL metacharacters/keywords, run against the *raw* input. Redundant with
   Sanitize's whitelist by the time something reaches this stage (a value that passed
   Sanitize literally can't contain `<` or `;`) -- the honest point of naming it
   separately is that it's a second, independent layer, not the only thing standing
   between a visitor and the database (that's parametrized queries, always).
3. **Test: Uniqueness** -- does this full name already exist. This is the *only* place
   uniqueness is checked -- the join form itself no longer hard-blocks a duplicate name
   (see below), so this stage is where that actually gets caught.
4. **Test: Profanity** -- does the name or any icebreaker answer contain a blocked word
   (hashed blocklist, see below).
5. **Build** -- assemble the spawn payload: a world position, the appearance render
   data, and the composed speech-bubble text for each of the 4 answers. No database
   writes.
6. **Deploy** -- actually apply the change: write the character row as live.
7. **Verify** -- read the row back and confirm it landed correctly (a genuine
   read-after-write check, not assumed).

## The icebreakers: the same 4 fixed questions for everyone, genuinely free-text answers

Every visitor answers the same 4 fixed, business-friendly questions (favorite food,
favorite movie, a hobby, an ideal weekend) -- there's no picking, and the set is
identical for every character. That's deliberate: it's what lets two characters who
wander close together in Production Town "match" on a topic and hold a real back-and-forth
(see "Conversations in Production Town" below). Each *answer* is real open text: a
visitor can type anything (within a length cap), and it goes through the same three
pipeline stages the name does -- Sanitize (charset whitelist tuned for a short sentence:
letters, digits, spaces, and basic punctuation), Security Scan (the raw-input pattern
scan), and Test: Profanity (the same hashed blocklist) -- rather than being waved through
as "just flavor text." This is the intentional core of the exercise: real open input
fields, cleaned and defended properly, not a workaround that avoids the problem by only
accepting pre-approved strings.

## Conversations in Production Town

Because every character answers the same 4 fixed questions, two characters who happen
to wander within a short distance of each other pause and take turns showing one line
each -- "Favorite food: Tacos" from one, then "Favorite food: Ramen" from the other, then
on to the next topic -- for a few seconds, with a dashed line drawn between them, before
drifting apart again (see `pipeline_town.js`'s `updateConversations`). It's a small
touch, but it only works *because* the questions are fixed and shared rather than picked
per visitor -- there'd be nothing to match on otherwise.

The town itself is one large, mostly obstacle-free open field (`buildCity`), not a dense
city grid -- it used to be a 4x3 grid of blocks each with 1-2 buildings, which sounds more
"town-like" but actively broke conversations: ordinary wandering and neighbor-seeking
steer straight toward a target with no pathfinding (only the one-time spawn-to-park walk
uses real pathfinding, see `findGridPath`), so a dense obstacle layout meant two
characters who'd genuinely noticed each other often couldn't actually close the distance,
bouncing off whatever wall sat between them instead. A handful of landmark buildings stay
along one edge purely as backdrop.

## Why this is a real pipeline, not a progress bar

- **Queued, not synchronous**: joining enqueues an RQ job (Redis) and returns immediately --
  see the [SRE Infra Layer](../sre_infra/README.md) for the queue itself.
- **Real stage transitions, persisted**: each stage writes a `PipelineRun` row (stage,
  pass/fail, timestamps) to Postgres -- this is what `/pipeline-analytics` queries.
- **Real, tested validation as the stage logic**: rather than shelling out to a fresh
  `pytest.main()` per run (slow, fragile inside a worker loop), each stage calls the
  exact same functions covered by `tests/test_validators.py` -- genuinely tested code
  running live, not a fake check with a different implementation than what's tested.
- **A live build-log feed**: each stage's `start` event carries the actual pseudo-commands
  it's about to run (e.g. the real shape of the SQL for Test: Uniqueness), printed to the
  page as it happens -- concrete feedback about what kind of thing is happening, not a
  spinner.
- **Visible failure path**: a duplicate full name, a blocked word, or an injection
  pattern fails visibly at the stage that caught it, with the actual reason shown, and
  never reaches Production Town. Uniqueness in particular is checked *only* here, not at
  submission time (see below) -- two visitors submitting the same name back-to-back are
  both accepted and genuinely race through the pipeline, and whichever one actually loses
  the race fails visibly at Test: Uniqueness instead of being silently turned away
  upfront.
- **Pushed live over Socket.IO**: every connected visitor sees every pipeline run in real
  time on the tracker table and build log, not just their own submission -- it's a
  shared world.

## Input validation (`app/services/validators.py`)

- **Sanitize stage**: `sanitize_name_part` -- letters/spaces/hyphens/apostrophes only for
  names; `sanitize_icebreaker_answers` -- letters/digits/spaces/basic punctuation, applied
  to all 4 free-text answers. Both reject HTML/script tags and most SQL metacharacters by
  construction, length-capped. Every query touching these values is parametrized
  regardless -- the whitelist is defense-in-depth, not the actual injection defense.
- **Security Scan stage**: `check_no_injection_patterns` -- an explicit regex scan for
  HTML/script tags and SQL metacharacters/keywords, run against raw pre-Sanitize input.
- **Test: Uniqueness stage**: `check_full_name_collision` (case-insensitive first+last
  match, hard block) -- called *only* from `pipeline.py`'s own stage, not from the join
  form's upfront validation (see below).
- **Test: Profanity stage**: `check_no_profanity` -- a small **hashed** blocklist
  (hashed rather than a plaintext wordlist, so the literal words aren't sitting in a
  public repo), checked against the name and every icebreaker answer.
- **Not checked upfront, on purpose**: `validate_join_request` (the join form's upfront
  gate) runs format/injection/profanity checks for good UX, but deliberately does **not**
  call `check_full_name_collision` -- uniqueness is the pipeline's own Test: Uniqueness
  stage's job, checked once the job actually runs, not before it's even enqueued. See
  that function's docstring for the reasoning.
- **Join-form-only, not a pipeline stage**: last-name collision is a separate,
  warn-and-confirm UX step ("A Koskela already exists -- continue anyway?") handled before
  a job is even enqueued -- it needs a human's yes/no, which doesn't fit an automated
  pass/fail stage, and it's a softer nudge (shared last name) than the hard full-name
  block Test: Uniqueness enforces.

## `/pipeline-analytics` -- the SQL showcase

Real hand-written SQL over `pipeline_runs` -- `GROUP BY` aggregations and window
functions (success rate over time, mean time between failures, slowest stage, rolling
7-day pass rate, appearance duplication counts), each shown with its actual query in a
`<details>` block. **Requires a live Postgres connection** (`DATE_TRUNC` and the window
frame syntax are Postgres dialect) -- see `app/services/analytics.py`'s module docstring
for why Postgres was chosen here specifically, unlike the rest of this site's SQLite
default.

## Key files

- `app/blueprints/pipeline_world/routes.py`
- `app/services/validators.py`, `pipeline.py`, `analytics.py`
- `app/models.py` -- `Character`, `PipelineRun`
- `app/templates/pipeline_world/index.html` (tracker + build log), `town.html` (viewer),
  `pipeline_analytics.html`
- `app/static/js/pipeline_tracker.js`, `pipeline_town.js`

## Tests

`tests/test_validators.py`, `tests/test_pipeline.py`, `tests/test_pipeline_world.py`,
`tests/test_analytics.py` -- the pipeline runs synchronously in tests (see
[SRE Infra Layer](../sre_infra/README.md)'s queue section) so a full sanitize-to-live run
is asserted on directly, no sleeps or polling needed. Analytics correctness tests that
need a live Postgres connection are gated to auto-skip here and run for real once pointed
at `docker-compose`'s postgres service.

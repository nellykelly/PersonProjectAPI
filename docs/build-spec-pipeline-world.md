# Projects 3 & 4: Pipeline World (SDLC Sim) + SRE Infra Layer

These two projects share one codebase/dataset but are presented as **separate portfolio pieces** — Project 3 demonstrates DevOps/CI-CD + SQL depth, Project 4 demonstrates infra/SRE patterns (queueing, caching, rate limiting) built on top of it. Splitting them lets each one carry its own clear narrative on the site instead of being buried as a sub-feature.

---

## Input Model (revised — no code execution, ever)

Visitors do **not** submit code. The only inputs are:

1. **Character name**
   - Must be globally unique (no two characters share an identical name)
   - **Last-name collision check**: if a new name shares a last name with an existing character, show a warning/confirmation prompt before allowing it ("A Koskela already exists — continue anyway?")
   - **Full-name collision (first + last both match)**: hard block, cannot submit
   - Standard sanitization: strip/reject HTML, script tags, SQL special characters, length limits, profanity filter — validate and parametrize on the backend regardless of frontend checks
2. **Character appearance** (sprite/color/outfit selection from a fixed set of options)
   - Duplicates are allowed
   - The world/UI should surface how many characters currently share the same appearance (e.g., a small counter or grouping — "4 characters look like this")

This input surface is intentionally tiny and fully validated server-side, so there's no code-execution or injection risk — the "commit" a visitor triggers is really just "a new character requests to join the world," which is what drives the pipeline described below.

---

## Project 3: Pipeline World (SDLC Visualizer)

**Concept:** A top-down world (Shimeji-style, but top-down instead of bottom-of-screen) where each visitor's character is added to the world only after passing through a real, visible SDLC-style pipeline — mirroring how a JPMC-style deploy pipeline works, just applied to "a character joining the world" instead of a code change.

**Flow:**
1. Visitor submits name + appearance (validated per above)
2. This event is enqueued (see Project 4 for the queue itself)
3. A worker picks it up and runs a real pipeline against it:
   - **Validation stage**: re-run server-side input validation (uniqueness, sanitization) as an explicit pipeline step, not just a pre-check
   - **Test stage**: run an actual small test suite (e.g., pytest checks confirming the character record is well-formed, appearance ID is valid, name passes the collision rules) — this can be a real, small, genuine test suite, not fake
   - **Build stage**: assemble the character's final "spawn payload" (position, sprite, stats)
   - **Deploy stage**: insert into the `characters` / `world_state` table and mark as live
4. Each stage transition is pushed to the frontend (Flask-SocketIO / WebSockets) in real time
5. The frontend renders the character as an actor walking from a "Validation Gate" → "Test Lab" → "Build Yard" → "Production Town" (or similar zone names), only appearing as a walking character in the main world once it clears "Deploy"
6. If a stage fails (e.g., a duplicate full name slipped through, or a test fails), the character visibly fails at that stage instead of silently erroring — this is good UX and also a good demo of failure-path handling, which is exactly what a real pipeline needs

**Why this is a strong showcase piece:** it's not a fake progress bar — it's a real event going through a real queue, a real (if small) test suite, and a real write to a real table, visualized as it happens. That's the actual point of a CI/CD pipeline, just made visible and fun instead of hidden in a Jenkins log.

### SQL / Analytics Component (this is the SQL showcase)

Build a `/pipeline-analytics` page on top of the `pipeline_runs` table (every stage transition logged with timestamps, pass/fail, stage name). This page should compute, via real SQL (not just ORM model calls):

- **Success rate over time** — daily/weekly pass rate, via `GROUP BY` + date truncation
- **Mean time between failures** — window function over ordered failed runs
- **Slowest stage** — average duration per stage, `GROUP BY` stage with `AVG(end_time - start_time)`
- **Rolling 7-day pass rate** — window function (`AVG(...) OVER (ORDER BY date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)`)
- **Appearance duplication counts** — `GROUP BY` appearance_id, `COUNT(*)`, feeding the "N characters look like this" UI element

This is genuine non-trivial SQL — joins across `characters` and `pipeline_runs`, aggregations, and window functions — which is exactly the gap your other projects don't cover on their own.

---

## Project 4: SRE / Infra Layer

**Concept:** The infrastructure underneath Pipeline World, presented as its own project because it demonstrates a distinct skill set (queueing, caching, load/abuse handling) that deserves its own write-up rather than being a footnote.

**Components:**

1. **Queue (Redis + RQ, or Celery + Redis)**
   - Every "character join" event goes onto a queue rather than being processed synchronously in the request/response cycle
   - This is the async processing story: the HTTP request returns immediately ("your character is joining — watch the pipeline"), while a worker processes the actual pipeline in the background
   - Demonstrates understanding of why synchronous processing doesn't scale for anything resource-intensive or bursty

2. **Cache (Redis)**
   - Current world state (list of live characters, positions, appearances) is cached in Redis rather than hitting Postgres on every page load
   - Cache invalidation/update happens when a character completes the pipeline and spawns
   - This is a real, defensible caching strategy (cache-aside pattern) — worth naming explicitly in the README

3. **Rate limiting (Redis)**
   - The "submit a character" endpoint is public and anonymous, so it needs abuse protection
   - Rate-limit character submissions per IP (e.g., via `Flask-Limiter` backed by Redis) to prevent someone from scripting thousands of join requests
   - This is a legitimate load-handling/abuse-prevention pattern that comes up directly in SRE interviews

**Why split this from Project 3:** Project 3's narrative is "I built a fun, real CI/CD visualizer." Project 4's narrative is "I built the queueing/caching/rate-limiting infrastructure that makes a public-facing, anonymous-write system safe and scalable under load." Those are two different competencies a hiring manager screens for separately (application/DevOps engineer vs. infra/SRE engineer), and keeping them as two clearly-labeled project write-ups lets the site speak to both audiences without diluting either pitch. The README for Project 4 can explicitly reference Project 3 as "the system this infra layer supports," so it's clear they're related without being the same pitch.

---

## Updated Full Project List (4 projects)

1. **Trading Simulator** — yfinance-backed public PnL tracker
2. **QR Quant Company Scorer** — composite scoring + backtesting
3. **Pipeline World** — SDLC visualizer / top-down world, real pipeline + SQL analytics
4. **SRE Infra Layer** — queue, cache, rate limiting underneath Project 3

## Resume-Corrected Notes for the Site

- Python is a **current, confirmed skill** used daily at JPMC — not a gap. Update site copy accordingly (do not frame these projects as "learning Python," frame them as "continuing to build with Python outside of work").
- Actual title is **Software Engineer II**, Corporate & Investment Banking — more specific than "Software Engineer," worth using the precise title.
- Resume also surfaces two more projects not yet reflected in the site outline: **Beeznest** (Ruby on Rails B2B platform, 2nd place at StreetCode Accelerator Demo Day) and **Timed-Squares** (JS/Processing + Python/Pygame puzzle game). These could round out an "earlier projects" or "archive" section on the Projects page — they're smaller than the four flagship builds but show range (Rails, game dev) and one has a concrete award attached, which is good social proof worth surfacing.

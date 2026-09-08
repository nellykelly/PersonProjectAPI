---
title: "Project: Pipeline World"
kind: project
---

# What it is

A CI/CD visualiser at /projects/pipeline-world. A visitor submits a "character" — a name,
a pick from a fixed appearance list, and free-text answers to four fixed icebreaker
questions, never any code — and watches it move through a real, queued, seven-stage
pipeline before it is allowed to exist in a live, shared top-down world.

# The pipeline

The seven stages are Sanitize, Security Scan, Test: Uniqueness, Test: Profanity, Build,
Deploy, and Verify. The join request is enqueued **unchecked** — nothing validates it at
the route. A worker, a separate process from the web server, picks up the job and runs
the stages. Each stage emits a start event over Socket.IO carrying the actual
pseudo-command it is about to run (which powers a live build-log feed every connected
visitor sees), calls the same validator functions that the pytest suite covers, and
writes a real `PipelineRun` row with pass/fail and timestamps.

A failure stops the pipeline there; no later stage runs, so the tracker table renders
those cells as "not reached" and a PASS can never appear to the right of a FAIL. Deploy
is the only stage that changes anything — it assigns a world position and marks the
character live. Verify is a genuine read-after-write check: it re-reads the row and
confirms it landed correctly, rather than assuming the write succeeded.

# Why validation happens in the pipeline, not at the route

The first version validated at the submission route, so an obviously-bad submission
never became a job. That quietly broke the product: a visitor who typed a blocked word
got a red error and **no pipeline run at all** — the single most interesting thing the
project does was the one thing nobody could watch happen. Now every rule lives in
exactly one place, the stage that owns it, and bad input produces a run that visibly
fails. "Fail fast" and "fail visibly" are not the same goal.

# The analytics page

/pipeline-analytics runs real hand-written PostgreSQL over the `pipeline_runs` history —
`GROUP BY` aggregations and window functions for success rate over time, mean time
between failures, slowest stage, and a rolling seven-day pass rate — with each query
shown in a collapsible block so it is checkable, not just claimed. This is the one part
of the site that genuinely requires Postgres rather than SQLite.

# What it demonstrates

A real queue and worker rather than a fake progress bar, a visible failure path,
websocket push to many clients, reusing tested code as the pipeline's own logic, and
non-trivial analytical SQL.

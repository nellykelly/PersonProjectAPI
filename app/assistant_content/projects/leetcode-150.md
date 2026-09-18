---
title: "Project: Top Interview 150 Tracker"
kind: project
---

# What it is

A personal interview-prep dashboard built over LeetCode's official Top Interview 150
problem list, organised by topic. The loop: open a problem, run a 20-minute timer
without looking at the solution, then compare your approach to the accepted one and
mark it Yes (got it) or No (needs another pass). Marks are mutually exclusive.

# How it's built

Account-gated: each signed-in account keeps its own board, stored server-side and kept
in sync through a small `/api/progress` JSON API (CSRF-protected, same pattern as the
rest of the site's write endpoints). The front end itself is vanilla JavaScript with no
framework and no build step -- a progress bar, per-topic stat counts, and the
20-minute countdown timer. A one-time import path exists for a board saved in
`localStorage` from before the tracker required an account: on first login it offers to
merge that local board in, filling in only the problems the account hasn't already
marked, never overwriting a server-side mark.

# What it demonstrates

A tight, dependency-free front-end build paired with a small, deliberately scoped
server API, and a real migration path (the local-to-account import) handled instead of
just abandoning whatever data existed before the accounts model landed.

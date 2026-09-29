# Documentation & Claude-context optimization -- 2026-09-28

Audit and restructuring of this repo's Markdown documentation, done to reduce
how much context Claude Code has to read (and how much it has to guess or
rediscover) while keeping full access to every piece of technical knowledge
that was already written down. Nothing was deleted; everything moved is still
in the tree, at a new, categorized path, with cross-references updated.

## 1. What the actual problem was

**Correction to the starting premise:** the brief this audit was run from
assumed "an old, bloated `CLAUDE.md` is wasting tokens." That premise was
checked first and found false. There was **no `CLAUDE.md` anywhere in this
repo** before this pass -- no persistent, always-loaded instruction file at
all. So this was never a trim-the-bloat job.

The real problems were:

1. **No always-loaded file at all.** Nothing guaranteed Claude would ever see
   the run/test commands, the Windows-gunicorn caveat, the single-gunicorn-
   process/SocketIO constraint, the `flask assistant reindex` gotcha, the
   `$`-escaping-in-`.env` gotcha, or the prior committed-secret incident,
   unless it happened to read `README.md` in full that session (20.3 KB) --
   or, worse, didn't, and rediscovered one of these the hard way (e.g. by
   proposing a second gunicorn worker, or debugging a "wrong password" that
   was actually a Compose interpolation bug already solved and documented).
2. **No navigation.** 27 in-scope Markdown files, several large
   (`docs/INTERVIEW-NOTES.md` 61 KB / 880 lines,
   `docs/build-spec-family.md` 34 KB / 655 lines,
   `docs/build-spec-tiny-jvm.md` 30 KB / 600 lines), sitting flat in `docs/`
   with no index distinguishing "read this to work here" from "historical,
   already built" from "rarely-needed deep reference." Finding the right one
   meant either already knowing the repo or reading several in full to find
   out they weren't the right one.
3. **One genuinely stale file, self-identified.** `HANDOVER.md` at the repo
   root: its own `TODO.md` already said "is a session artifact and its
   'nothing committed' section is now stale ... delete it or move it to
   `docs/`." Actioned directly (see below).
4. **Two build-spec docs superseded by better current docs**, with nothing
   marking that: `docs/build-spec-pipeline-world.md` and
   `docs/build-spec-family.md` were both pre-build implementation authorities
   for features that are now fully built and live, with much better as-built
   references already existing (`app/blueprints/pipeline_world/README.md`,
   `app/blueprints/sre_infra/README.md`, `app/blueprints/family/README.md`,
   `docs/INTERVIEW-NOTES.md` §1). Nothing said "read the newer doc instead."

No file's technical content was found to be *factually* stale beyond
`HANDOVER.md` -- this repo is under active, near-daily development (every
in-scope file was last touched within the ~5 weeks before this audit), and
the blueprint READMEs, warehouse docs, and active build-spec docs all check
out against the current code and `git log`.

## 2. Constraint discovered mid-audit: two docs are load-bearing from code

Before moving anything, I grepped the whole tree for path references to each
candidate-to-move file. Two are cited **by exact path from application source
that this audit is not permitted to edit**:

- `docs/build-spec-tiny-jvm.md` -- cited from `app/__init__.py`,
  `app/blueprints/tiny_jvm/routes.py`, and **`app/templates/tiny_jvm/index.html`**
  (a live, user-facing template on the deployed site).
- `docs/build-spec.md` -- cited from `app/services/pricing.py` and
  `app/services/net_monitor.py` (code comments).
- `docs/family-design.md` -- cited from `app/static/family/family.css`.

These three were **left at their exact original paths**, not moved, even
though `build-spec.md` is otherwise "archive-shaped" (historical, all three
projects it specs are built). Each got a short status banner instead (see
below) so a reader gets the "this is historical / here's the current doc"
signal without a path change that would silently break a code comment or,
in the tiny-jvm case, a rendered page. This is the one place I chose
"annotate in place" over "move to the ideal category," and I'm flagging it
explicitly rather than treating it as a clean archive.

## 3. New structure

```
CLAUDE.md                              NEW -- lean, always-loaded root file
README.md                              unchanged in substance; doc-index
                                        section and 3 stale internal links updated
TODO.md                                one item checked off (see below)
docs/
  build-spec.md                        KEPT IN PLACE (cited from app/services/*.py);
                                        added a historical-status banner
  build-spec-tiny-jvm.md                KEPT IN PLACE (cited from app code + a live
                                        template); unchanged -- it's still accurate
  family-design.md                      KEPT IN PLACE (cited from app/static/family/family.css);
                                        unchanged -- still the current design record
  SECURITY-NOTE.md                      KEPT IN PLACE (small, high-value, 4 other
                                        files already link it; no benefit to moving)
  DOCUMENTATION_OPTIMIZATION_REPORT.md  NEW -- this file
  archive/                              NEW category: completed/superseded, historical
    HANDOVER.md                          moved from repo root
    build-spec-family.md                 moved, banner added
    build-spec-pipeline-world.md         moved, banner added
  reference/                            NEW category: deep, rarely-needed material
    INTERVIEW-NOTES.md                   moved
    notes-rag-hashing-brainstorm.md      moved
app/blueprints/*/README.md              UNCHANGED placement (already correct: code-
                                        adjacent, conditionally loaded). One file
                                        (trading/README.md) got one internal link fixed.
legacy/, market-data-warehouse/,        UNCHANGED -- already well-organized,
tools/tinyjvm/                         conditionally-loaded sub-project docs
app/assistant_content/*.md              UNCHANGED, untouched, out of scope --
                                        this is runtime data the app serves as the
                                        assistant's own knowledge base, not developer docs
```

No deep taxonomy: two new subfolders (`archive/`, `reference/`), not the
`docs/systems/` / `docs/decisions/` / `docs/development/` full suggested
shape, because `docs/development/` would have just duplicated
`README.md` (already a comprehensive, current run/test/deploy doc) and
`docs/systems/` would have been empty or near-empty once the load-bearing
files stayed put and the blueprint READMEs (correctly) stayed next to
their code.

## 4. Every change made

**Created:**
- `CLAUDE.md` (repo root, 142 lines / ~7.1 KB) -- what the app is, how to
  run/test it, five critical constraints (secrets handling + the prior
  incident, the `.env` `$`-escaping gotcha, the single-gunicorn-process/
  SocketIO constraint, the assistant-reindex gotcha, the runtime-data-vs-docs
  distinction for `app/assistant_content/`), core conventions, a documentation
  navigation index, and rules for where future docs should go.
- `docs/DOCUMENTATION_OPTIMIZATION_REPORT.md` -- this file.
- `docs/archive/`, `docs/reference/` -- new subfolders.

**Moved (`git mv`, history preserved), each with a short banner added at the
top explaining why it moved and pointing to the current doc:**
- `HANDOVER.md` → `docs/archive/HANDOVER.md`
- `docs/build-spec-family.md` → `docs/archive/build-spec-family.md`
- `docs/build-spec-pipeline-world.md` → `docs/archive/build-spec-pipeline-world.md`
- `docs/INTERVIEW-NOTES.md` → `docs/reference/INTERVIEW-NOTES.md` (no banner
  needed -- it wasn't stale, just miscategorized)
- `docs/notes-rag-hashing-brainstorm.md` → `docs/reference/notes-rag-hashing-brainstorm.md`
  (same)

**Edited (content preserved, links/status fixed):**
- `README.md` -- doc-navigation section rewritten to describe the new
  `docs/` layout and point to `CLAUDE.md`; 3 stale paths fixed
  (`docs/build-spec-pipeline-world.md` → `docs/archive/...`,
  `docs/INTERVIEW-NOTES.md` → `docs/reference/...`, and the opening
  paragraph's build-spec links marked historical).
- `TODO.md` -- checked off the `HANDOVER.md` item ("delete it or move it to
  `docs/`"), noting it was moved to `docs/archive/HANDOVER.md`.
- `docs/build-spec.md` -- added a historical-status banner explaining it's
  kept at this path only because two `.py` files cite it by path.
- `docs/archive/HANDOVER.md`, `docs/archive/build-spec-family.md`,
  `docs/archive/build-spec-pipeline-world.md` -- each got a short
  "Archived, here's the current doc instead" banner prepended; original
  content below it is untouched.
- `docs/reference/notes-rag-hashing-brainstorm.md` -- one internal link
  (`docs/INTERVIEW-NOTES.md` → `docs/reference/INTERVIEW-NOTES.md`) fixed.
- `app/blueprints/trading/README.md` -- same one-link fix.

**Deleted:** nothing. Every byte of prior content still exists in the tree
(moved files) or in git history (nothing was force-rewritten).

**Explicitly NOT touched:** `app/assistant_content/*.md` (runtime data, out
of scope per the brief), all `app/blueprints/*/README.md` placements (already
correct), `market-data-warehouse/*.md` (already well-organized, correctly
conditionally-loaded), `legacy/README.md`, `tools/tinyjvm/HOW_TO_WRITE_A_PROGRAM.md`,
`.nelson/` (explicitly out of scope -- historical orchestration logs from a
different tool; a few of them reference old doc paths like
`docs/INTERVIEW-NOTES.md` and `docs/build-spec-family.md`, but those are
point-in-time session records, not live navigation, so left as-is), and the
`.claude/skills/` directory (empty in this repo; the `personaldeployment`/
`personalstartup` skills that appear in the environment's skill listing
actually live one directory up, in the parent staging folder that's out of
scope for this repo -- noted here for awareness, not acted on).

## 5. Always-loaded size, honestly

There is no way to give a precise token count without running the actual
tokenizer, so these are byte/line measurements with a rough, clearly-labeled
token estimate.

- **Before:** 0 bytes. No `CLAUDE.md`, no other auto-loaded instruction file
  existed anywhere in this repo. The honest "before" number for guaranteed
  always-loaded content is zero.
- **After:** `CLAUDE.md` is 7,070 bytes / 142 lines -- roughly 1,600-1,900
  tokens by typical English-prose density (~3.7-4.4 bytes/token), not
  independently verified against the tokenizer.

**This is a real increase in guaranteed baseline load, not a decrease** --
worth stating plainly rather than dressing it up. The trade being made is:
~1,700 tokens paid on every single session, in exchange for not needing to
read `README.md` in full (20.8 KB) or explore source code from scratch to
answer "how do I run this," "how do I test this," or "why is there a single
gunicorn worker" -- and in exchange for the constraints that were previously
undiscoverable except by hitting the bug (the `.env` `$`-escaping issue, the
silent-reindex-miss issue) now being guaranteed-visible instead of
session-roulette. Whether that trade is worth it depends on session
frequency and how often those specific facts matter; for a single-maintainer
repo under active development, guaranteed visibility of five real gotchas
seemed worth ~1,700 tokens. The other lever pulled -- reorganizing `docs/`
into `archive/`/`reference/`/current -- has no always-loaded cost at all; its
benefit is entirely in *on-demand* reads being smaller and better-targeted
(e.g. a task about Pipeline World no longer risks reading the superseded
34 KB `build-spec-family.md`-equivalent by mistake, or the 61 KB
`INTERVIEW-NOTES.md` when only the README was needed).

## 6. Rules for where future docs go (also written into `CLAUDE.md` §"Where
new docs go")

- Per-subsystem technical doc → that blueprint's own `README.md`
  (`app/blueprints/<name>/README.md`), not `docs/`.
- Cross-cutting, currently-relevant design/decision doc → `docs/`, root
  level.
- Superseded or completed planning doc → `docs/archive/`.
- Rarely-needed deep reference (interview prep, brainstorm logs,
  postmortems) → `docs/reference/`.
- Session-handover/scratch notes → don't commit to the repo root; either
  start them in `docs/archive/` and say so, or fold the durable parts into
  the relevant README/TODO and let the rest live in chat history.
- Before moving *any* existing doc in a future pass: grep the whole tree
  (not just `docs/` and other `.md` files) for its path first. Two files in
  this repo are cited from live application code/templates by exact path;
  moving them without checking would have silently broken a rendered page.

## 7. Recommended Claude workflow going forward

1. `CLAUDE.md` loads automatically -- it now carries the run/test commands
   and the five constraints that used to require either luck or a bug to
   discover.
2. For anything beyond that, use `CLAUDE.md`'s documentation index rather
   than globbing `docs/` -- it tells you which file is current, which is
   historical, and which blueprint README to read for a given subsystem,
   so you read one targeted file instead of several to find the right one.
3. When working inside a specific blueprint, read that blueprint's
   `README.md` first; it's more current and more precise than the
   corresponding `docs/build-spec-*` (where one still exists).
4. Treat `docs/archive/` as "why it was built this way," not "how it
   currently works" -- read it only when the *reasoning*, not the current
   behavior, is what's needed.
5. If a task turns up a newly-stale doc or a genuinely dead cross-reference,
   fix it in the same pass (small, additive) rather than letting it
   accumulate -- this repo's docs were caught early enough (5-week staleness
   ceiling) that one pass was enough; a future pass will be cheaper if that
   holds.

## 8. Ambiguous calls flagged for a human decision

- **`docs/build-spec.md` staying at its path.** I added a banner rather than
  moving it to `docs/archive/`, because `app/services/pricing.py` and
  `app/services/net_monitor.py` cite it by path in comments and I'm not
  permitted to edit those `.py` files in this pass. If a future pass is
  allowed to touch source comments, the cleaner end state is: update those
  two comments to point at `docs/archive/build-spec.md`, then move the file.
  Left as-is for now rather than guessing that's acceptable.
- **`docs/family-design.md` and `docs/build-spec-tiny-jvm.md`** are in the
  same position (cited from `family.css` and from a live template/routes,
  respectively) but are *not* archive-shaped content -- both are still the
  current, accurate reference for active work, so no banner was needed
  there, only the path-preservation constraint applied.
- **The `.claude/skills/` mismatch.** The environment reports two
  project-scoped skills (`personaldeployment`, `personalstartup`) as
  available "from `PersonProjectAPI-master/.claude/skills`," but that
  directory is empty in this repo; the real `SKILL.md` files live in the
  parent staging folder (`S:\PersonProjectAPI-master\.claude\skills\`),
  which is one level up and explicitly out of scope for this audit. Not
  acted on, but worth a human decision at some point: either those skills
  belong inside this repo (so they travel with clones of the actual git
  repo, and so `CLAUDE.md` could point to them) or the environment's skill
  discovery is picking up a stale/misrouted path. I did not investigate
  further since it's a Claude Code configuration question, not a Markdown
  documentation one.
- **`.nelson/` files referencing old doc paths.** A handful of files under
  `.nelson/missions/` (out of scope, explicitly excluded) reference the old,
  pre-move paths for `docs/INTERVIEW-NOTES.md` and `docs/build-spec-family.md`.
  Left untouched per the exclusion, but flagging that they are now
  technically stale pointers, in case those logs are ever consulted for
  navigation rather than as a historical record.

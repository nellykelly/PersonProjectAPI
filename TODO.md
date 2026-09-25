# TODO

Site- and dev-environment-specific work for this repo. Career, job search, and
anything else cross-project lives in the master list at `S:\TODO.md`.

Check items off (`- [x]`) instead of deleting them, and move finished
sections to **Done** at the bottom now and then.

Last swept for loose ends: 2026-09-24.

## This site

- [ ] **Review and commit the uncommitted work in the tree**: about 47 files
      covering assistant evals/guard/cache/observability, Jev, the application
      drafter, freehire source, migrations and tests. Nothing is committed yet
- [ ] Delete the stray file `S:claude-scratchredcellfull.diff` in the repo root
      (a scratch diff saved to the wrong path because of the drive-letter colon)
- [ ] **Cron jobs written, not yet installed on the VPS.** `scripts/cron/hera-crontab`
      + `scripts/cron/install-cron.sh` wire up `flask assistant eval` (daily 03:00),
      `flask job-tracker sweep` (daily 03:20), and `flask job-tracker rescore`
      (hourly) -- real cron, not an app-level poller, to keep RAM flat on the 4GB
      Hetzner box. Still needs `ssh nelson@<host> && ./scripts/cron/install-cron.sh`
      run for real before Job Discovery/Hera-eval history stop being "on demand only"
- [ ] Tiny JVM steps 5-6 (see `docs/build-spec-tiny-jvm.md` §11): Wokwi ESP32
      firmware view, standalone GitHub repo + README for the VM/toolchain
- [ ] `HANDOVER.md` is a session artifact and its "nothing committed" section is
      now stale (the warehouse is committed). Delete it or move it to `docs/`
- [ ] Market warehouse ideas, optional (from HANDOVER): fundamentals via
      `INFO_FIELDS`, earnings calendar fact, a live Snowflake run
- [ ] Jev is built but **off on purpose** (no `TYPESAFE_API_KEY`); only turn it on
      if you decide to pay for it

## Candidate new projects (ideas, not committed)

Future portfolio-project ideas for this site, gathered for later evaluation. Not
scoped, sized, or committed to yet -- check against real target-company problems
(see the master TODO's portfolio-project framework entry) before building any of them.

- [ ] **10 production-grade AI project ideas** (Bashiri Smith, seen 2026-09-24), pitched
      as mapping to what senior AI-engineering hires get judged on, not generic demos:
  - [ ] LLM observability -- a failure-forensics tool that traces a broken pipeline
        to the exact step that failed
  - [ ] Cost optimization -- an LLM cost autopilot that routes each request to the
        cheapest model that can still handle it
  - [ ] Multimodal AI -- a document processor that turns messy PDFs/scanned docs
        into clean, structured data
  - [ ] Prompt engineering -- a prompt versioning + A/B-testing platform that proves
        which prompt actually performs best
  - [ ] AI regression testing -- evals that run on every commit and block a change
        that degrades quality
  - [ ] Model deployment -- a serving platform that ships new model versions as
        canaries and auto-rolls-back on a quality drop
  - [ ] AI security -- a red-team harness that attacks your own app, plus a firewall
        that blocks the attacks that work
  - [ ] Computer vision -- a defect-detection pipeline from raw images to a deployed
        API, with active learning
  - [ ] Reinforcement learning -- an agent that learns to make decisions in a custom
        environment and beats a baseline
  - [ ] Model benchmarking -- a harness that ranks any model on quality, latency,
        and cost, so the choice is evidence-based, not vibes
  - Note: #4, #5, and #6 overlap with pieces this site already has in miniature
    (`flask assistant eval`, the Groq fallback chain) -- worth a look before treating
    any of these as greenfield.
  - (He's also running a "comment SKILL for the full breakdown + 5 more" lead-gen
    funnel -- noted for awareness, not something to act on.)
- [ ] **12 AI Engineer project ideas** (Bashiri Smith, second video, seen 2026-09-24;
      "build these and you're hired at $150,000+"). Only 10 are actually distinct --
      items 11 and 12 in the on-screen montage repeat items 5 and 10 verbatim:
  - [ ] Model Regression Detection System
  - [ ] Failure Forensics Tool for AI Pipelines
  - [ ] Self-Healing Technical Documentation
  - [ ] LLM Output Arbitration System
  - [ ] LLM Gateway with Rate Limiting, Fallback Routing, and Observability
  - [ ] Fine-Tuning Pipeline with LoRA on a Domain-Specific Dataset
  - [ ] Prompt Versioning and A/B Testing Platform
  - [ ] Text-to-SQL Interface with Guardrails and Hallucination Detection
  - [ ] Automated Eval Dataset Generator from Production Logs
  - [ ] AI Feature Flag System with Gradual Rollout and Quality Monitoring
  - Overlaps with the 10-item list just above (same creator, overlapping material):
    Failure Forensics Tool = LLM observability; Prompt Versioning and A/B Testing =
    Prompt engineering, exact repeat; Model Regression Detection and the Eval Dataset
    Generator cluster with AI regression testing; the LLM Gateway covers Cost
    optimization plus more; the Feature Flag System is the same family as canary
    Model deployment. Treat the two lists as one overlapping idea pool, not 20
    separate options, when picking one to actually scope.
  - (Ends with "comment 'hired' to get the full list" -- same funnel pattern, not
    something to act on.)

## Dev environment / security

- [ ] Rotate the old Google OAuth client secret + revoke its refresh token
      (history is scrubbed, rotation is still yours: `docs/SECURITY-NOTE.md`)
- [ ] Delete the old Claude config copy at `C:\Users\nicep\.claude` (639 MB).
      This session confirmed it runs from `S:\claude-config`, so it's safe to remove
- [ ] Review or discard the unapproved `autoMode.environment` draft in
      `S:\claude-config\settings.json` (left from the paused `/auto-mode-setup`)

## Done

- [x] GitHub default branch: `master` now contains `bio-network-redesign` and
      prod runs from `master`, so the old "switch default branch" to-do is resolved

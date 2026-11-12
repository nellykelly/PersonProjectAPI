# practice -- ATL-101 dbt + SQL practice sandbox

`/practice/`: a password-gated, private practice environment for one
realistic data-engineering ticket (ATL-101: let the delta-sync export run
for a chosen list of policies). Look-alikes of Jira, Snowflake, the dbt
Cloud IDE, Airflow, S3 and GitHub, a coach panel with hints, and an
auto-grader. Real dbt-core + dbt-duckdb do the work. Everything in it is
fictional. Never linked, `noindex` (meta + `X-Robots-Tag`), robots.txt
`Disallow: /practice`, not in the sitemap.

The sandbox itself is a separate program in [`sandbox/`](../../../sandbox/README.md)
running as its own compose service. This blueprint is only the gate and a
reverse proxy.

## Routes

| Route | What |
|---|---|
| `GET/POST /practice/` | Password gate. Correct password sets `session["practice_unlocked"]` and, if missing, a random `session["practice_sandbox_id"]` (32 hex). Rate-limited (`PRACTICE_UNLOCK_RATE_LIMIT`, only failed attempts count). |
| `GET/POST /practice/app/<path>` | Proxied to `PRACTICE_SANDBOX_URL/<path>` with `X-Sandbox-Session: <sandbox id>`. `/practice/app/` serves the UI's `index.html`. Locked: pages redirect to the gate, `api/*` and POSTs get 401 JSON. |
| `GET /practice/lock` | Drops the unlock flag but keeps the sandbox id, so unlocking again finds your work. |

## Request flow

```
browser --(cookie)--> Caddy --> web (Flask)  /practice/app/api/dbt
                                   |  gate: unlocked? X-Sandbox: 1 on POST? body <= 512 KB?
                                   v
                     sandbox:8765  /api/dbt   X-Sandbox-Session: 3f9c...  (internal network only)
                                   |  per-session lock -> data/sessions/3f9c.../ -> real dbt
```

- **One sandbox per browser.** The id lives in the signed Flask session
  cookie, so two browsers (or a phone and a laptop) never share or overwrite
  work. The sandbox service trusts the header because only `web` can reach
  it: `sandbox_net` is `internal: true` and the port is never published.
- **CSRF.** The gate form carries a token. The proxy view is
  `csrf.exempt` because the UI posts JSON from `fetch()`; instead every
  POST must carry `X-Sandbox: 1` (the UI's `api()` helper always sends
  it). A cross-site page can't set a custom header without a CORS
  preflight, which this app never answers.
- **Timeouts.** `dbt build` and the grader can take ~1 minute; the proxy's
  read timeout is `PRACTICE_PROXY_TIMEOUT` (600 s). That holds one of
  gunicorn's 8 threads for the duration (see GOTCHAS.md), which is fine for a
  private tool. If the service is down: a 503 "starting or offline" page,
  never a 500.

Business logic for the proxy is in `app/services/practice_sandbox.py`.

## Config

| Var | Default | Notes |
|---|---|---|
| `PRACTICE_PASSWORD_HASH` | unset -> 503 | Werkzeug hash. `$$`-escape every `$` in compose's `.env`. |
| `PRACTICE_SANDBOX_URL` | `http://sandbox:8765` | compose sets this explicitly; local dev uses `http://127.0.0.1:8765`. |
| `PRACTICE_UNLOCK_RATE_LIMIT` | `10 per hour` | |
| `PRACTICE_PROXY_RATE_LIMIT` | `300 per minute` | per IP, on every proxied request |
| `PRACTICE_PROXY_TIMEOUT` | `600` | seconds |

Make a hash: `python -c "from werkzeug.security import generate_password_hash as g; print(g(input()))"`.

## Running locally (no Docker)

```bash
cd sandbox && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python tools/server.py          # builds the template once (~15 s), then :8765
# in .env: PRACTICE_PASSWORD_HASH=..., PRACTICE_SANDBOX_URL=http://127.0.0.1:8765
flask --app wsgi run                           # -> http://localhost:5000/practice/
```

## Tests

`tests/test_practice.py`: fails closed, nothing proxied while locked,
per-browser ids differ, id survives lock/unlock, POST needs `X-Sandbox`,
body limit, sandbox-down is 503, noindex/robots/sitemap. The sandbox
service is stubbed. Its own regression check (baseline 2/7, answer key
7/7) is described in `sandbox/README.md`.

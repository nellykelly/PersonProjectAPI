# clients -- private client sites

`/clients/<slug>/`: full, password-gated copies of client websites, so a
client (and whoever they share the password with) can view the build.
Never linked, `noindex` (meta + `X-Robots-Tag`), robots.txt
`Disallow: /clients`.

## How a site gets here

Each client site is a **static export** committed under
`app/client_sites/<slug>/` (so the Dockerfile's `COPY app` ships it). It's
built by `scripts/build_client_<slug>.sh`, which copies the client's
source to a temp dir (the client project itself is never modified),
applies the sub-path patches (`basePath`, plus the plain-string URLs
`basePath` doesn't rewrite), runs `next build`, and replaces the export.
The script fails if any link in the output is missing the base path.

**To update a site after the client project changes:** re-run its build
script, run `pytest tests/test_clients.py`, commit, deploy.

## Serving + gate

Flask serves every file of the export (pages *and* assets) through
`clients.site`, not Caddy, so nothing is reachable while locked. That costs
a gunicorn thread per asset request -- fine at preview traffic; see
GOTCHAS.md on the thread budget. Resolution follows Next's
`trailingSlash` layout (`recipes/` -> `recipes/index.html`), `.mjs` is
served as `text/javascript` (MapLibre's worker), unknown paths get the
export's own `404.html`.

Same fail-closed model as `/documentation` and `/family`: one Werkzeug
hash per client in the environment, its own session flag
(`client_<slug>_unlocked`); unset hash -> 503. A locked request for any
path redirects to `/clients/<slug>/`, which shows the gate; a correct
POST there unlocks (PRG). `/clients/<slug>/lock` clears it. Unlock POSTs
are rate-limited by `CLIENTS_UNLOCK_RATE_LIMIT` (only failures count).
HTML is `no-store`; hashed `_next/static/` assets cache privately.

## Adding a client

1. Add `slug -> (display name, config key)` to `CLIENTS` in `routes.py`.
2. Add the config key to `app/config.py` and `.env.example`.
3. Copy `scripts/build_client_espressocaribbean.sh`, adjust `SRC`, `BASE`
   and the patch lines to that project, run it.
4. Set the hash in the server's `.env` (`$$`-escape the `$` for compose --
   see GOTCHAS.md):
   `python -c "from werkzeug.security import generate_password_hash as g; print(g(input()))"`

## Clients

| Slug | Config key | Source |
|---|---|---|
| `espressocaribbean` | `CLIENT_ESPRESSOCARIBBEAN_PASSWORD_HASH` | `S:\Caribeen coffee\04-development\src` (Next.js 15) |

Tests: `tests/test_clients.py` (run against the committed export).

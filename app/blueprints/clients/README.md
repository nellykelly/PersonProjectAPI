# clients -- private client pages

`/clients/<slug>`: password-gated pages shown to freelance clients
(handover docs, previews). Never linked, `noindex` (meta + `X-Robots-Tag`),
`Cache-Control: private, no-store`, `Disallow: /clients` in robots.txt.

## Gate

Same fail-closed model as `/documentation` and `/family`. Each client has
its own Werkzeug hash in the environment and its own session flag
(`client_<slug>_unlocked`), so one client's password never opens another's
page. An unset hash means that page returns 503. GET shows the gate, a
POST of the right password sets the flag and redirects back (PRG);
`/clients/<slug>/lock` clears it. Unlock POSTs are rate-limited by
`CLIENTS_UNLOCK_RATE_LIMIT` (default 10/hour, only failed attempts count).

## Adding a client

1. Add an entry to `CLIENTS` in `routes.py`: slug -> (display name, config
   key, template).
2. Add the config key to `app/config.py` (`os.environ.get(...)`) and a
   blank line for it in `.env.example`.
3. Write `app/templates/clients/<slug>.html` extending `clients/_layout.html`.
4. Generate the hash and set it in the server's `.env` (`$$`-escape the `$`
   for docker-compose -- see GOTCHAS.md):
   `python -c "from werkzeug.security import generate_password_hash as g; print(g(input()))"`

## Clients

| Slug | Config key | Content |
|---|---|---|
| `espressocaribbean` | `CLIENT_ESPRESSOCARIBBEAN_PASSWORD_HASH` | "Trade Winds" module handover, transcribed from `S:\Caribeen coffee\04-development\HANDOVER.md` (a static copy -- edit the template if the source doc changes) |

Tests: `tests/test_clients.py`.

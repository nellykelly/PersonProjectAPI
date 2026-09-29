# Gotchas

Failure modes in this repo that are silent, look like a different bug, or
only show up in production. Every entry here was learned the hard way once
already — read this in full before touching deploy, secrets, `.env`,
migrations, static assets, or `app/assistant_content/`. Each links to the
full writeup in `README.md` if you need more than the summary.

## Secrets

**Never put a secret in chat, a command's visible output, or a file written
by hand in a session.** They live only in `.env` (gitignored), sourced at
runtime. This repo has a real prior incident — a Google OAuth client secret
and refresh token were committed and pushed publicly, since purged from git
history and rotated. One item from it is **still open**: rotating the old
OAuth client (tracked in `TODO.md`). Full detail:
[`docs/SECURITY-NOTE.md`](docs/SECURITY-NOTE.md).

## `.env` values containing `$`

Docker Compose interpolates `$` inside `.env`. A value containing `$` (e.g.
`DOCS_PASSWORD_HASH`, a Werkzeug `scrypt:N:r:p$salt$digest` hash) must be
written as `$$` for a literal `$`, or Compose reads `$salt`/`$digest` as
undefined variable references, substitutes empty strings, and the container
receives a silently truncated value. It fails by *looking correctly
configured* and rejecting every password — not a config-file error, a
wrong-password bug, until checked at the point of use rather than the point
of definition. `SECRET_KEY`/`POSTGRES_PASSWORD` are plain hex here so they're
not at risk, but audit any new secret that isn't. Full detail: README, "A
`.env` gotcha worth knowing before it happens again".

## `gunicorn` is a single process, deliberately

`-w 1 --worker-class gthread --threads 8` in the Dockerfile is intentional.
Flask-SocketIO's `threading` async mode keeps a client's session in the
memory of whichever process created it; a second gunicorn worker would
round-robin a session's later requests onto a process that never heard of it
and return `400 unknown session`, silently breaking Pipeline World's and the
traffic board's live updates. Don't add `-w N` without re-reading README,
"Why static assets are served by Caddy, not gunicorn".

## Static assets: `Cache-Control: no-cache`, not `max-age=...`

Filenames here carry no content hash, so nothing tells a browser a deploy
changed a file. `max-age=3600` let a visitor keep running the *previous* JS
for up to an hour after a fix shipped — a fix that looked broken only because
the browser was serving a cached copy of the old file. `no-cache` still lets
the browser cache and revalidate via `If-None-Match`; Caddy answers `304`
from the ETag, so an unchanged asset costs one small conditional request, not
a re-download. Also note: the 8 gunicorn threads are a shared, finite budget,
which is why static assets are served by Caddy directly off a read-only bind
mount instead of going through gunicorn at all.

## `app/assistant_content/` changes need a manual reindex

`docker compose exec -T web flask assistant reindex` — nothing triggers this
automatically. Forgetting it is silent: no error, the assistant just keeps
answering from the old corpus, including confidently denying a project or
fact that a content file already covers. Run it on every deploy that could
plausibly have touched content, not just ones you're sure did (the corpus is
small and always rebuilt whole, so there's no cost to running it
unnecessarily). See README, "Deploy loop".

## Migration deploy order

```bash
docker compose exec -T web flask db upgrade
```
A migration generated against a freshly-built image, copied to the host, and
applied *without* rebuilding the image a second time runs from a build that
predates the migration file on disk — `flask db upgrade` reports success
while quietly sitting one migration behind. Rebuild on both sides of the
copy, not just the first. See README, "Applying a migration in production".

## Multi-domain TLS: one bad subdomain drags both down

A single certificate covering `nelsonkoskela.dev` and `www.nelsonkoskela.dev`
fails as one unit. If `www`'s DNS isn't correct yet, its ACME validation
failure drags the *whole order* down to Let's Encrypt's untrusted staging CA
— so the bare domain, whose DNS was fine, ends up serving an untrusted cert
too. Diagnose by checking the actual issuer, not the padlock icon:
```bash
openssl s_client -connect nelsonkoskela.dev:443 </dev/null 2>/dev/null | openssl x509 -noout -issuer
# looking for O=Let's Encrypt, not a "Fake LE" staging root
```
Fix is getting each domain's DNS correct before adding it to the Caddy site
block, not after. See README, "TLS/DNS" section.

## Windows can't run gunicorn locally

Use `flask --app wsgi run` for local dev on Windows instead. See `CLAUDE.md`,
"Run it".

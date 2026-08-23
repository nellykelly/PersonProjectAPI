# Legacy code (not part of the running app)

This is what the repo contained before the rebuild described in
[`docs/build-spec.md`](../docs/build-spec.md): a single-file Flask app mixing a blog, a
Google Calendar OAuth integration, and a half-broken Yahoo Finance route, plus two bundled
HTML templates. None of it is imported by the new `app/` package or run by
`wsgi.py`/`Dockerfile` -- kept here for history rather than deleted outright.

- `App.py`, `GoogleCal.py`, `quickstart.py`, `calenderHandler.py`, `yahoo.py` -- the old
  routes/integrations.
- `templates/` -- old pages, including `index.html` / `courses.html` (the licensed
  "pink" Colorlib education template the build spec says *not* to use -- see
  `README-colorlib-license.txt`, its attribution can't be removed without a paid
  license) and `indexp.html` (the dark HTML5 UP "Dimension" template the new site's
  design is derived from).
- `static_colorlib_backup/` -- the full original `static/` tree (mostly Colorlib assets
  for the retired pink template).
- `venv/`, `__pycache__/`, `instance/blog.db` -- disposable build artifacts, kept rather
  than deleted just in case, not meant to be regenerated or used.

**Not here:** `app/credentials (2).json` and `app/token.pickle` (the Google OAuth
secrets) are gone -- removed from the working tree, purged from git history entirely
(`git-filter-repo`, force-pushed), and no longer on disk. `credentials*.json` /
`token.pickle` stay in `.gitignore` as a backstop, not because either file still exists.
See [`docs/SECURITY-NOTE.md`](../docs/SECURITY-NOTE.md) for the full remediation --
the one step that's still on you regardless of any of this is rotating the actual
OAuth client, since a git history rewrite doesn't undo whatever already saw the secret
while it was public.

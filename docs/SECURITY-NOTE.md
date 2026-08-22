# Security note: rotate the old Google OAuth credentials

While rebuilding this site, two real secrets were found sitting in the legacy
codebase (now retired -- see `legacy/`):

- `app/credentials (2).json` -- a Google OAuth **client secret** (`client_id` /
  `client_secret`) for the old Google Calendar integration.
- `app/token.pickle` -- a pickled `google.oauth2.credentials.Credentials`
  object containing a live **refresh token** for that same OAuth client.

A GitHub API tree listing for this repo showed `credentials (2).json` as a
tracked file, which strongly suggests it has already been pushed to the
public `nellykelly/PersonProjectAPI` repository at some point.

## What to do

1. **Rotate/revoke the OAuth client** in the Google Cloud Console
   (APIs & Services -> Credentials) regardless of anything done in this
   rebuild -- if the secret is or was ever public, treat it as compromised.
2. **Revoke the refresh token** tied to that client (Google Account ->
   Security -> Third-party access), since `token.pickle` grants ongoing
   Calendar access until revoked, independent of the client secret.
3. If the file was committed to git history, a plain `git rm` on a new
   commit does **not** remove it from history -- that requires a history
   rewrite (`git filter-repo` / BFG) and a force-push, which is destructive
   and was intentionally **not** done automatically here. Do this yourself
   if/when you're ready, ideally after rotating the credentials above so
   the exposed values are already dead.

## What this rebuild already did

- Left both files in place on disk (didn't delete potential in-progress
  work) but added `credentials*.json` and `token.pickle` to `.gitignore`
  going forward.
- Did not include either file, or the Google Calendar integration that used
  them, in the new `app/` package at all -- see `legacy/` for the retired
  code.

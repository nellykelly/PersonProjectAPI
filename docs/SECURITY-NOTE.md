# Security note: Google OAuth credential exposure -- history cleaned, rotation still yours to do

While rebuilding this site, two real secrets were found sitting in the legacy
codebase (now retired -- see `legacy/`):

- `app/credentials (2).json` -- a Google OAuth **client secret** (`client_id` /
  `client_secret`) for the old Google Calendar integration.
- `app/token.pickle` -- a pickled `google.oauth2.credentials.Credentials`
  object containing a live **refresh token** for that same OAuth client.

A GitHub API tree listing for this repo showed `credentials (2).json` as a
tracked file, confirming it had been pushed to the public
`nellykelly/PersonProjectAPI` repository.

## Status: git history has been rewritten and force-pushed

This has already been done, not left as a future step:

1. Removed from the current tree first, with a normal commit (non-destructive,
   safe to do immediately, done before anything riskier).
2. Full history rewrite: cloned fresh into an isolated scratch directory, ran
   `git-filter-repo` to strip the files from **every** commit -- this also
   caught a *second*, older exposure the first pass missed
   (`courses/credentials (2).json`, from before the code was reorganized under
   `app/`), found by diffing which paths had ever existed across all of
   history rather than assuming the current path was the only one that ever
   held the file.
3. Verified the purge two ways before pushing: no matching *paths* remained in
   any commit, and (more rigorously) `git grep` across every blob in history
   for the actual client ID string, to catch the case where the file might
   have been renamed rather than deleted-and-recreated at a new path.
4. Force-pushed the rewritten history only after that verification passed.
5. Both files are also gone from disk now, not just gitignored -- the Google
   Calendar integration that used them was retired entirely (see `legacy/`),
   and `credentials*.json` / `token.pickle` stay in `.gitignore` as a backstop
   against anything similar happening again, not because either file still
   exists to be caught by it.

## What's still on you

**Rotating the actual credentials.** Deleting a secret from git history does not
undo whatever already saw it while it was public -- rewriting history only stops
*future* clones/forks from finding it, it doesn't invalidate the secret itself.
If this hasn't been done yet:

1. **Rotate/revoke the OAuth client** in the Google Cloud Console
   (APIs & Services -> Credentials) -- treat it as compromised regardless of the
   history cleanup above.
2. **Revoke the refresh token** tied to that client (Google Account ->
   Security -> Third-party access), since it grants ongoing Calendar access
   until revoked, independent of the client secret.

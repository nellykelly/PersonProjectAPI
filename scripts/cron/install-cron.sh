#!/usr/bin/env bash
# Installs (or updates) this project's scheduled jobs into the current
# user's crontab. Run this ON THE VPS, as the user that owns
# ~/PersonProjectAPI and runs `docker compose` there (see README.md's
# Hosting section) -- not on a local dev machine.
#
#   ssh nelson@<host>
#   cd ~/PersonProjectAPI && ./scripts/cron/install-cron.sh
#
# Idempotent: re-running this replaces only the block between the two
# marker lines below, so it's safe to run again after pulling an updated
# scripts/cron/hera-crontab -- it will not duplicate entries, and it will
# not touch any other cron jobs already on this box.
set -euo pipefail

BEGIN_MARK="# BEGIN hera-cron (managed by scripts/cron/install-cron.sh -- do not hand-edit this block)"
END_MARK="# END hera-cron"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SOURCE_FILE="$SCRIPT_DIR/hera-crontab"

if [ ! -f "$SOURCE_FILE" ]; then
  echo "error: $SOURCE_FILE not found" >&2
  exit 1
fi

mkdir -p "$PROJECT_DIR/logs"

# Existing crontab, or nothing if the user has none yet (crontab -l exits
# nonzero in that case -- not a real error here).
existing="$(crontab -l 2>/dev/null || true)"

# Strip any previous hera-cron block (everything from BEGIN_MARK through
# END_MARK, inclusive) so re-running this script updates in place instead
# of appending a duplicate copy every time.
stripped="$(printf '%s\n' "$existing" | awk -v b="$BEGIN_MARK" -v e="$END_MARK" '
  $0 == b { skip = 1 }
  !skip { print }
  $0 == e { skip = 0 }
')"

{
  printf '%s\n' "$stripped"
  echo "$BEGIN_MARK"
  cat "$SOURCE_FILE"
  echo "$END_MARK"
} | crontab -

echo "Installed. Current crontab:"
echo "---"
crontab -l
echo "---"
echo "Logs will land in $PROJECT_DIR/logs/cron-*.log (overwritten each run -- see hera-crontab's own comment for why)."

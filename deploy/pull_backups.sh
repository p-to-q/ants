#!/usr/bin/env bash
# Pull backups from the server and verify them locally.
# Usage:  deploy/pull_backups.sh DIR
# Uses deploy/remote_deploy.sh pull-backups underneath, then verifies each new backup with
# the same routine `ants restore --check` uses (antslab.backup.verify_backup).
# Appends one line per run to DIR/PULL.log.  Exits non-zero if any verification failed or
# the newest backup of any arm is older than 36 h.
set -euo pipefail
cd "$(dirname "$0")/.."

DIR="${1:?local directory for pulled backups}"
mkdir -p "$DIR"

PYTHON="${ANTS_PYTHON:-${PWD}/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

TS="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
FAIL=0

# pull from the server (uses ANTS_HOST, ANTS_USER, ANTS_KEY)
deploy/remote_deploy.sh pull-backups "$DIR" || { echo "$TS pull FAILED" >> "$DIR/PULL.log"; exit 1; }

PULLED=0
VERIFIED=0
BAD=0
NEWEST_AGE_H=0

for arm_dir in "$DIR"/*/backups; do
  [ -d "$arm_dir" ] || continue
  arm="$(basename "$(dirname "$arm_dir")")"
  # verify each sidecar that we have not verified before (check if the backup file exists)
  for sidecar in "$arm_dir"/ledger-*.json; do
    [ -f "$sidecar" ] || continue
    PULLED=$((PULLED + 1))
    if "$PYTHON" -c "
import json, sys
sys.path.insert(0, 'src')
from antslab.backup import verify_backup
m = json.loads(open('$sidecar').read())
verify_backup('$arm_dir', m)
" 2>/dev/null; then
      VERIFIED=$((VERIFIED + 1))
    else
      BAD=$((BAD + 1))
    fi
  done
  # newest backup age
  AGE=$("$PYTHON" -c "
import json, time, sys, glob
sys.path.insert(0, 'src')
from antslab.backup import list_backups
bks = list_backups('$arm_dir')
if bks:
    print(round((time.time() - bks[0]['created']) / 3600, 1))
else:
    print(9999)
" 2>/dev/null || echo "9999")
  # track the worst (oldest) age
  if "$PYTHON" -c "import sys; sys.exit(0 if float('$AGE') > float('$NEWEST_AGE_H') else 1)" 2>/dev/null; then
    NEWEST_AGE_H="$AGE"
  fi
  # anchor chain head check
  ANCHOR_FILE="$arm_dir/anchors.jsonl"
  if [ -f "$ANCHOR_FILE" ]; then
    # get the last anchor head from the file
    ANCHOR_HEAD=$("$PYTHON" -c "
import json
lines = open('$ANCHOR_FILE').read().strip().splitlines()
if lines:
    print(json.loads(lines[-1]).get('head', '')[:16])
else:
    print('')
" 2>/dev/null || echo "")
    # get the chain head of the newest backup
    BK_HEAD=$("$PYTHON" -c "
import json, sys
sys.path.insert(0, 'src')
from antslab.backup import list_backups
bks = list_backups('$arm_dir')
print(bks[0]['head'][:16] if bks else '')
" 2>/dev/null || echo "")
    if [ -n "$ANCHOR_HEAD" ] && [ -n "$BK_HEAD" ] && [ "$ANCHOR_HEAD" != "$BK_HEAD" ]; then
      echo "FAIL $arm: anchor head $ANCHOR_HEAD != newest backup head $BK_HEAD" >> "$DIR/PULL.log"
      FAIL=1
    fi
  fi
done

LINE="$TS pulled=$PULLED verified_ok=$VERIFIED bad=$BAD newest_backup_age_h=$NEWEST_AGE_H"
echo "$LINE" >> "$DIR/PULL.log"
echo "$LINE"

if [ "$BAD" -gt 0 ]; then
  echo "FAIL: $BAD backup(s) failed verification"
  FAIL=1
fi

if "$PYTHON" -c "import sys; sys.exit(0 if float('$NEWEST_AGE_H') > 36 else 1)" 2>/dev/null; then
  echo "FAIL: newest backup is ${NEWEST_AGE_H}h old (threshold 36h)"
  FAIL=1
fi

exit "$FAIL"

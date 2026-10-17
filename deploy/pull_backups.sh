#!/usr/bin/env bash
# Pull backups from the server and verify them locally.
# Usage:  deploy/pull_backups.sh DIR
# Uses deploy/remote_deploy.sh pull-backups underneath, then verifies each new backup with
# the same routine `ants restore --check` uses (antslab.backup.verify_backup).
# Appends one line per run to DIR/PULL.log.  Exits non-zero if any verification failed or
# the newest backup of any arm is older than 36 h (WORST_AGE_H tracks the oldest-newest).
set -euo pipefail
cd "$(dirname "$0")/.."

DIR="${1:?local directory for pulled backups}"
mkdir -p "$DIR"

PYTHON="${ANTS_PYTHON:-${PWD}/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

TS="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
FAIL=0

# pull from the server (uses ANTS_HOST, ANTS_USER, ANTS_KEY)
# ANTS_NO_PULL=1 verifies what is already in DIR (tests; a second look at an earlier pull) without touching the network.
if [ -z "${ANTS_NO_PULL:-}" ]; then
  deploy/remote_deploy.sh pull-backups "$DIR" || { echo "$TS pull FAILED" >> "$DIR/PULL.log"; exit 1; }
fi

PULLED=0
VERIFIED=0
BAD=0
WORST_AGE_H=0

# The server keeps backups at /var/lib/ants/backups/<arm>/ (ants-backup@.service), so after the pull they are at DIR/backups/<arm>/. A
# workdir-local DIR/<arm>/backups is read too (a backup made with a different --dest).
# Series backups are one level deeper: DIR/backups/series/<name>/<workdir>/ (series_backup.sh).
for arm_dir in "$DIR"/backups/* "$DIR"/backups/series/*/* "$DIR"/*/backups; do
  [ -d "$arm_dir" ] || continue
  [ "$arm_dir" = "$DIR/backups/series" ] && continue                      # a container of series, not a backup directory
  case "$arm_dir" in "$DIR"/backups/series/*/*) ;; "$DIR"/backups/series/*) continue ;; esac   # nor is a series name
  if [ "$(basename "$arm_dir")" = "backups" ]; then arm="$(basename "$(dirname "$arm_dir")")"; else arm="$(basename "$arm_dir")"; fi
  # verify each sidecar
  for sidecar in "$arm_dir"/ledger-*.json; do
    [ -f "$sidecar" ] || continue
    PULLED=$((PULLED + 1))
    if "$PYTHON" -I -c "
import json, sys
sys.path.insert(0, 'src')
from antslab.backup import verify_backup
m = json.loads(open(sys.argv[1]).read())
verify_backup(sys.argv[2], m)
" "$sidecar" "$arm_dir" 2>/dev/null; then
      VERIFIED=$((VERIFIED + 1))
    else
      BAD=$((BAD + 1))
    fi
  done
  # newest backup age for this arm
  AGE=$("$PYTHON" -I -c "
import json, time, sys
sys.path.insert(0, 'src')
from antslab.backup import list_backups
bks = list_backups(sys.argv[1])
print(round((time.time() - bks[0]['created']) / 3600, 1) if bks else 9999)
" "$arm_dir" 2>/dev/null || echo "9999")
  # track the worst (oldest newest-backup) across arms
  WORST_AGE_H=$("$PYTHON" -I -c "
import sys
a, b = float(sys.argv[1]), float(sys.argv[2])
print(a if a > b else b)
" "$AGE" "$WORST_AGE_H")
  # anchor chain head check
  ANCHOR_FILE="$arm_dir/anchors.jsonl"
  if [ -f "$ANCHOR_FILE" ]; then
    ANCHOR_HEAD=$("$PYTHON" -I -c "
import json, sys
lines = open(sys.argv[1]).read().strip().splitlines()
print(json.loads(lines[-1]).get('head', '')[:16] if lines else '')
" "$ANCHOR_FILE" 2>/dev/null || echo "")
    BK_HEAD=$("$PYTHON" -I -c "
import json, sys
sys.path.insert(0, 'src')
from antslab.backup import list_backups
bks = list_backups(sys.argv[1])
print(bks[0]['head'][:16] if bks else '')
" "$arm_dir" 2>/dev/null || echo "")
    if [ -n "$ANCHOR_HEAD" ] && [ -n "$BK_HEAD" ] && [ "$ANCHOR_HEAD" != "$BK_HEAD" ]; then
      echo "FAIL $arm: anchor head $ANCHOR_HEAD != newest backup head $BK_HEAD" >> "$DIR/PULL.log"
      FAIL=1
    fi
  fi
done

LINE="$TS pulled=$PULLED verified_ok=$VERIFIED bad=$BAD worst_backup_age_h=$WORST_AGE_H"
echo "$LINE" >> "$DIR/PULL.log"
echo "$LINE"

if [ "$VERIFIED" -eq 0 ] && [ "$BAD" -eq 0 ]; then
  echo "FAIL: no backup was found or verified under $DIR (a pull that verifies nothing is not a check)"
  FAIL=1
fi

if [ "$BAD" -gt 0 ]; then
  echo "FAIL: $BAD backup(s) failed verification"
  FAIL=1
fi

STALE=$("$PYTHON" -I -c "import sys; print(1 if float(sys.argv[1]) > 36 else 0)" "$WORST_AGE_H")
if [ "$STALE" = "1" ]; then
  echo "FAIL: oldest newest-backup is ${WORST_AGE_H}h old (threshold 36h)"
  FAIL=1
fi

exit "$FAIL"

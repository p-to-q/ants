#!/usr/bin/env bash
# Back up every workdir ledger under a series base directory.
# Usage: series_backup.sh <series-name>
# Called by ants-series-backup@.service.  Fails loudly on any error.
set -euo pipefail

NAME="${1:?series name required}"
[[ "$NAME" =~ ^[a-z0-9_]{1,40}$ ]] || { echo "bad series name: $NAME" >&2; exit 1; }

BASE="/var/lib/ants/series/$NAME"
DEST="/var/lib/ants/backups/series/$NAME"
ANTS=/opt/ants/.venv/bin/ants

[ -d "$BASE" ] || { echo "series base not found: $BASE" >&2; exit 1; }

FAIL=0
for ledger in "$BASE"/*/ledger.sqlite; do
    [ -e "$ledger" ] || continue
    wd="$(dirname "$ledger")"
    wd_name="$(basename "$wd")"
    mkdir -p "$DEST/$wd_name"
    if ! "$ANTS" backup --workdir "$wd" --dest "$DEST/$wd_name" --keep 7 --gzip; then
        echo "FAIL: backup of $wd_name" >&2
        FAIL=1
    fi
done

if [ "$FAIL" -ne 0 ]; then
    echo "series_backup: one or more backups failed" >&2
    exit 1
fi
echo "series_backup: $NAME done"

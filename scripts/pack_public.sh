#!/usr/bin/env bash
# Build the PUBLIC tarball: no internal/ notes, no environments, no runs, no secrets.
# The secret scan runs on the staging tree AND on the finished tarball (what is actually shipped); the tarball only reaches dist/ if both pass.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
export COPYFILE_DISABLE=1                      # macOS: no AppleDouble (._*) members that carry extended attributes
OUT="dist/antslab-$(date +%Y%m%d).tar.gz"; mkdir -p dist
STAGE=$(mktemp -d); trap 'rm -rf "$STAGE"' EXIT
if git rev-parse --git-dir >/dev/null 2>&1; then
  # in a checkout, ship exactly what is tracked: a scratch file in the working tree can never end up in a package
  git ls-files -z | rsync -a --from0 --files-from=- ./ "$STAGE/antslab/"
else
  rsync -a --exclude internal --exclude .git --exclude build --exclude .venv --exclude runs --exclude dist --exclude data/raw --exclude data/processed \
    --exclude '*.sqlite*' --exclude '*.env' --exclude '.env*' --exclude '*.pem' --exclude '*.key' --exclude .DS_Store --exclude '._*' \
    --exclude __pycache__ --exclude .pytest_cache --exclude .ruff_cache --exclude '*.egg-info' ./ "$STAGE/antslab/"
fi
if [ -e "$STAGE/antslab/internal" ]; then echo "REFUSING: internal/ leaked into the package"; exit 1; fi
if ! "$PY" -I scripts/scan_secrets.py "$STAGE/antslab"; then echo "REFUSING: secret-like content found in the staging tree (listed above)"; exit 1; fi
TMP="$OUT.partial"; trap 'rm -rf "$STAGE" "$TMP"' EXIT
tar -C "$STAGE" -czf "$TMP" antslab
if ! "$PY" -I scripts/scan_secrets.py --tar "$TMP"; then echo "REFUSING: secret-like content found in the final tarball (listed above); nothing was written"; exit 1; fi
if tar -tzf "$TMP" | grep -qE '(^|/)internal(/|$)'; then echo "REFUSING: internal/ is in the tarball"; exit 1; fi
mv "$TMP" "$OUT"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1)); files: $(tar -tzf "$OUT" | wc -l)"

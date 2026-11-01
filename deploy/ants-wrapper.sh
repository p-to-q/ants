#!/bin/sh
# /usr/local/bin/ants on the server: run ants as the service user, so that it reads the ledgers it owns and never leaves root-owned files
# behind, and keep the terminal settings so that the interface (`ants`) works over ssh.
GATE=/usr/local/lib/ants/deploy_gate.py
if [ -f "$GATE" ]; then
  if [ "${1:-}" = deploy-status ]; then
    shift
    exec /usr/bin/python3 "$GATE" status "$@"
  fi
  /usr/bin/python3 "$GATE" check || exit $?
fi
# Public, root-owned view settings; never put credentials in this file.
if [ -r /etc/ants-view.conf ]; then . /etc/ants-view.conf; fi
export ANTS_SERIES_DIR="${ANTS_SERIES_DIR:-/var/lib/ants/series}"
if [ "$(id -un)" = "ants" ]; then
  exec /opt/ants/.venv/bin/ants "$@"
fi
if [ "$(id -u)" = "0" ]; then
  exec runuser -u ants -- env ANTS_SERIES_DIR="$ANTS_SERIES_DIR" TERM="${TERM:-xterm-256color}" LANG="${LANG:-C.UTF-8}" COLUMNS="${COLUMNS:-}" LINES="${LINES:-}" /opt/ants/.venv/bin/ants "$@"
fi
exec sudo -u ants env ANTS_SERIES_DIR="$ANTS_SERIES_DIR" TERM="${TERM:-xterm-256color}" LANG="${LANG:-C.UTF-8}" /opt/ants/.venv/bin/ants "$@"

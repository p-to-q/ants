#!/bin/sh
# /usr/local/bin/ants on the server: run ants as the service user, so that it reads the ledgers it owns and never leaves root-owned files
# behind, and keep the terminal settings so that the interface (`ants`) works over ssh.
if [ "$(id -un)" = "ants" ]; then
  exec /opt/ants/.venv/bin/ants "$@"
fi
if [ "$(id -u)" = "0" ]; then
  exec runuser -u ants -- env TERM="${TERM:-xterm-256color}" LANG="${LANG:-C.UTF-8}" COLUMNS="${COLUMNS:-}" LINES="${LINES:-}" /opt/ants/.venv/bin/ants "$@"
fi
exec sudo -u ants env TERM="${TERM:-xterm-256color}" LANG="${LANG:-C.UTF-8}" /opt/ants/.venv/bin/ants "$@"

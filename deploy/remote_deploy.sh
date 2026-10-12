#!/usr/bin/env bash
# Deploy ants to the server over SSH (key auth). Idempotent. Usage:
#   deploy/remote_deploy.sh preflight     read-only inspection of the server
#   deploy/remote_deploy.sh install       upload + install (creates user 'ants', venv from the hashed lock, systemd units; starts nothing)
#   deploy/remote_deploy.sh data          fetch the dev-tier datasets ON the server, verify hashes against the local manifest, build
#   deploy/remote_deploy.sh smoke [N]     run N evaluations (default 60) as the service user and print the live view
#   deploy/remote_deploy.sh start         enable + start the service (arm 'portfolio') and the janitor timer
#   deploy/remote_deploy.sh logs | status | stop
# It never edits sshd, the firewall, passwords or authorized_keys.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${ANTS_HOST:?set ANTS_HOST to the server address}"; USER_="${ANTS_USER:?set ANTS_USER to the ssh user (needs passwordless sudo)}"
KEY="${ANTS_KEY:?set ANTS_KEY to the path of the ssh private key}"
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15 "$USER_@$HOST")
CT=/opt/ants/contracts/tinyts.toml
run_as_service() { "${SSH[@]}" "sudo -n -u ants bash -lc 'cd /opt/ants && $*'"; }
case "${1:-preflight}" in
  preflight)
    "${SSH[@]}" 'hostname; id; echo "--- cpu/mem/disk"; nproc; grep -m1 "model name" /proc/cpuinfo; free -m | sed -n 1,3p; df -h / | tail -1; swapon --show | head -2
                 echo "--- python"; python3 --version; echo "--- sudo (non-interactive)"; sudo -n true && echo SUDO_OK || echo NO_SUDO
                 echo "--- listening"; ss -ltn | awk "NR>1{print \$4}" | sort -u | tr "\n" " "; echo; echo "--- antslab present?"; ls /opt/ants 2>/dev/null | head -3 || true' ;;
  install)
    # Everything root will execute is staged in a private (mode 700) directory of the ssh user, not in the world-writable /tmp, and its
    # hash is checked on the server before `sudo bash` runs it: another local user cannot swap the installer between extraction and execution.
    PKG=$(ls -t dist/antslab-*.tar.gz | head -1)
    SUM=$({ sha256sum "$PKG" 2>/dev/null || shasum -a 256 "$PKG"; } | cut -d' ' -f1)
    "${SSH[@]}" 'rm -rf "$HOME/.ants_deploy" && mkdir -m 700 "$HOME/.ants_deploy"'
    scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes "$PKG" "$USER_@$HOST:.ants_deploy/antslab.tar.gz"
    "${SSH[@]}" 'set -e; D="$HOME/.ants_deploy"; echo "'"$SUM"'  $D/antslab.tar.gz" | sha256sum -c - >/dev/null
                 mkdir -m 700 "$D/src" && tar -xzf "$D/antslab.tar.gz" -C "$D/src" && sudo -n bash "$D/src/antslab/deploy/install.sh" "$D/src/antslab"
                 sudo -n install -m 640 -o ants -g ants /opt/ants/contracts/tinyts_real.toml '"$CT"' && echo "contract installed"; rm -rf "$D"' ;;
  data)
    run_as_service 'bash scripts/fetch_data.sh dev && .venv/bin/ants data build --catalog data/catalog.toml --names etth1,ettm1,weather,exchange && .venv/bin/ants data verify --partial' ;;
  smoke)
    N="${2:-60}"; [[ "$N" =~ ^[0-9]{1,6}$ ]] || { echo "N must be a number"; exit 1; }
    run_as_service ".venv/bin/ants run --contract $CT --workdir /var/lib/ants/smoke --arm portfolio --seed 1 --max-evals $N && .venv/bin/ants top --contract $CT --workdir /var/lib/ants/smoke --arm portfolio --once && .venv/bin/ants doctor --contract $CT --workdir /var/lib/ants/smoke --arm portfolio" ;;
  start)   "${SSH[@]}" 'sudo -n systemctl enable --now ants@portfolio.service ants-janitor.timer ants-health.timer && systemctl is-active ants@portfolio' ;;
  status)  "${SSH[@]}" 'systemctl is-active ants@portfolio; systemctl show ants@portfolio -p MemoryCurrent -p CPUUsageNSec | tr "\n" " "; echo' ;;
  logs)    "${SSH[@]}" 'journalctl -u ants@portfolio -n 40 --no-pager' ;;
  stop)    "${SSH[@]}" 'sudo -n systemctl disable --now ants@portfolio.service ants-janitor.timer ants-health.timer' ;;
  *) echo "unknown command"; exit 1 ;;
esac

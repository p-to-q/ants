#!/usr/bin/env bash
# Deploy ants to the server over SSH (key auth). Idempotent. Usage:
#   deploy/remote_deploy.sh preflight     read-only inspection of the server
#   deploy/remote_deploy.sh install       upload + install (creates user 'ants', venv from the hashed lock, systemd units; starts nothing)
#   deploy/remote_deploy.sh data [tier]   fetch the datasets of a tier (dev|standard|extended|all) ON the server, verify against the pinned manifest, build
#   deploy/remote_deploy.sh smoke [N]     run N evaluations (default 60) as the service user and print the live view
#   deploy/remote_deploy.sh llmkey FILE   install an llm.env (mode 600, owner ants) as /etc/ants/llm.env; never printed, never in a unit
#   deploy/remote_deploy.sh start [ARM..] enable + start one service per arm (default: portfolio) with its janitor, health and backup timers
#   deploy/remote_deploy.sh pull-backups DIR   copy /var/lib/ants/*/backups (and anchors.jsonl) off the server into DIR
#   deploy/remote_deploy.sh logs [ARM] | status | stop [ARM..]
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
    T="${2:-dev}"; [[ "$T" =~ ^(dev|standard|extended|all)$ ]] || { echo "tier: dev|standard|extended|all"; exit 1; }
    run_as_service ".venv/bin/ants data fetch --tier $T --catalog /opt/ants/data/catalog.toml && .venv/bin/ants data build --catalog /opt/ants/data/catalog.toml --present && .venv/bin/ants data verify --partial --catalog /opt/ants/data/catalog.toml" ;;
  smoke)
    N="${2:-60}"; [[ "$N" =~ ^[0-9]{1,6}$ ]] || { echo "N must be a number"; exit 1; }
    run_as_service ".venv/bin/ants run --contract $CT --workdir /var/lib/ants/smoke --arm portfolio --seed 1 --max-evals $N && .venv/bin/ants top --contract $CT --workdir /var/lib/ants/smoke --arm portfolio --once && .venv/bin/ants doctor --contract $CT --workdir /var/lib/ants/smoke --arm portfolio" ;;
  llmkey)
    F="${2:?path of the llm.env to install}"; [ -f "$F" ] || { echo "no such file"; exit 1; }
    scp -q -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes "$F" "$USER_@$HOST:.ants_llm.env"
    "${SSH[@]}" 'sudo -n install -m 600 -o ants -g ants "$HOME/.ants_llm.env" /etc/ants/llm.env && shred -u "$HOME/.ants_llm.env" 2>/dev/null || rm -f "$HOME/.ants_llm.env"; stat -c "%a %U %s bytes" /etc/ants/llm.env' ;;
  start)
    shift; ARMS=("${@:-portfolio}")
    for a in "${ARMS[@]}"; do [[ "$a" =~ ^[a-z_]{1,32}$ ]] || { echo "bad arm name"; exit 1; }; done
    UNITS=(); for a in "${ARMS[@]}"; do UNITS+=("ants@$a.service" "ants-janitor@$a.timer" "ants-health@$a.timer" "ants-backup@$a.timer"); done
    "${SSH[@]}" "sudo -n systemctl enable --now ${UNITS[*]} && for a in ${ARMS[*]}; do systemctl is-active ants@\$a; done" ;;
  status)  "${SSH[@]}" 'for u in $(systemctl list-units "ants@*" --no-legend | cut -d" " -f1); do echo "$u $(systemctl is-active $u) $(systemctl show $u -p MemoryCurrent -p WatchdogUSec -p NRestarts | tr "\n" " ")"; done; systemctl list-timers "ants-*" --no-legend | head' ;;
  logs)    "${SSH[@]}" "journalctl -u ants@${2:-portfolio} -n 40 --no-pager" ;;
  stop)    shift; ARMS=("${@:-portfolio}"); UNITS=(); for a in "${ARMS[@]}"; do UNITS+=("ants@$a.service" "ants-janitor@$a.timer" "ants-health@$a.timer" "ants-backup@$a.timer"); done
           "${SSH[@]}" "sudo -n systemctl disable --now ${UNITS[*]}" ;;
  pull-backups)
    D="${2:?local directory}"; mkdir -p "$D"
    rsync -a -e "ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes" --rsync-path="sudo -n rsync" "$USER_@$HOST:/var/lib/ants/" "$D/" --include='*/' --include='backups/***' --exclude='*' && ls -R "$D" | head -20 ;;
  *) echo "unknown command"; exit 1 ;;
esac

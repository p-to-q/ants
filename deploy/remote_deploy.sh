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
    # Nothing of the experiments is touched by a deploy: the code tree is replaced, /var/lib/ants (ledgers, backups, archive) never is. As a
    # belt for the braces, every ledger gets a verified backup first, and the deploy stops if one fails.
    "${SSH[@]}" 'set -e; for w in /var/lib/ants/*/ledger.sqlite; do [ -e "$w" ] || continue; d=$(dirname "$w"); n=$(basename "$d"); [ -x /opt/ants/.venv/bin/ants ] || break
                 sudo -n -u ants /opt/ants/.venv/bin/ants backup --workdir "$d" --dest "/var/lib/ants/backups/$n" --keep 14 --gzip >/dev/null || { echo "pre-deploy backup of $n failed: not deploying"; exit 1; }; done; echo "pre-deploy backups ok"'
    "${SSH[@]}" 'rm -rf "$HOME/.ants_deploy" && mkdir -m 700 "$HOME/.ants_deploy"'
    scp -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes "$PKG" "$USER_@$HOST:.ants_deploy/antslab.tar.gz"
    "${SSH[@]}" 'set -e; D="$HOME/.ants_deploy"; echo "'"$SUM"'  $D/antslab.tar.gz" | sha256sum -c - >/dev/null
                 mkdir -m 700 "$D/src" && tar -xzf "$D/antslab.tar.gz" -C "$D/src" && sudo -n bash "$D/src/antslab/deploy/install.sh" "$D/src/antslab"
                 sudo -n install -m 640 -o ants -g ants /opt/ants/contracts/tinyts_real.toml '"$CT"' && echo "contract installed"
                 echo "$(date -Is) $(/opt/ants/.venv/bin/ants --version) package-sha256 '"$SUM"'" | sudo -n tee -a /var/lib/ants/DEPLOY.log >/dev/null; rm -rf "$D"' ;;
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
    shift
    if [ "${1:-}" = "--registry" ]; then
      shift
      REG="${1:-deploy/experiments.toml}"
      # read running experiments from the TOML registry (stdlib python on the dev machine)
      ARMS=()
      while IFS= read -r name; do
        ARMS+=("$name")
      done < <(python3 -c "
import sys
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib
with open(sys.argv[1], 'rb') as f:
    d = tomllib.load(f)
for e in d.get('experiment', []):
    if e.get('status') == 'running':
        print(e['name'])
" "$REG")
      [ ${#ARMS[@]} -gt 0 ] || { echo "no running experiments in $REG"; exit 1; }
      echo "registry: starting ${ARMS[*]}"
    else
      ARMS=("${@:-portfolio}")
    fi
    for a in "${ARMS[@]}"; do [[ "$a" =~ ^[a-z_]{1,32}$ ]] || { echo "bad arm name: $a"; exit 1; }; done
    UNITS=(); for a in "${ARMS[@]}"; do UNITS+=("ants@$a.service" "ants-janitor@$a.timer" "ants-health@$a.timer" "ants-backup@$a.timer"); done
    "${SSH[@]}" "sudo -n systemctl enable --now ${UNITS[*]} && for a in ${ARMS[*]}; do systemctl is-active ants@\$a; done" ;;
  status)  "${SSH[@]}" 'for u in $(systemctl list-units "ants@*" --no-legend | cut -d" " -f1); do echo "$u $(systemctl is-active $u) $(systemctl show $u -p MemoryCurrent -p WatchdogUSec -p NRestarts | tr "\n" " ")"; done; systemctl list-timers "ants-*" --no-legend | head' ;;
  logs)    "${SSH[@]}" "journalctl -u ants@${2:-portfolio} -n 40 --no-pager" ;;
  stop)    shift; ARMS=("${@:-portfolio}"); UNITS=(); for a in "${ARMS[@]}"; do UNITS+=("ants@$a.service" "ants-janitor@$a.timer" "ants-health@$a.timer" "ants-backup@$a.timer"); done
           "${SSH[@]}" "sudo -n systemctl disable --now ${UNITS[*]}" ;;
  pull-backups)
    D="${2:?local directory}"; mkdir -p "$D"
    rsync -a -e "ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes" --rsync-path="sudo -n rsync" "$USER_@$HOST:/var/lib/ants/" "$D/" --include='*/' --include='backups/***' --exclude='*' && ls -R "$D" | head -20 ;;
  soak)
    echo "=== soak report $(date -Is) ==="
    "${SSH[@]}" 'for u in $(systemctl list-units "ants@*" --no-legend --plain | awk "{print \$1}"); do
      arm=${u#ants@}; arm=${arm%.service}
      started=$(systemctl show "$u" -p ActiveEnterTimestamp --value)
      restarts=$(systemctl show "$u" -p NRestarts --value)
      watchdog=$(journalctl -u "$u" --since "7 days ago" --no-pager -p warning --output=short-iso 2>/dev/null | grep -c watchdog || true)
      bk_age=$(sudo -n -u ants /opt/ants/.venv/bin/ants backups --workdir "/var/lib/ants/$arm" --json 2>/dev/null | python3 -c "import json,sys,time; d=json.load(sys.stdin); print(round((time.time()-d[0][\"created\"])/3600,1) if d else \"none\")" 2>/dev/null || echo "?")
      chain=$(sudo -n -u ants /opt/ants/.venv/bin/ants status --contract '"$CT"' --workdir "/var/lib/ants/$arm" --arm "$arm" 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(\"ok\" if d.get(\"chain_ok\") else \"BROKEN\")" 2>/dev/null || echo "?")
      disk=$(df -h /var/lib/ants 2>/dev/null | awk "NR==2{print \$4}" || echo "?")
      printf "%-24s active_since=%-24s NRestarts=%-4s watchdog_events=%-4s backup_age_h=%-6s chain=%-6s disk_free=%s\n" "$arm" "$started" "$restarts" "$watchdog" "$bk_age" "$chain" "$disk"
    done' ;;
  drill)
    DTYPE="${2:?drill type (kill9)}"; DARM="${3:?arm name}"
    [[ "$DARM" != "all" ]] || { echo "drill refuses 'all'; name one arm"; exit 1; }
    [[ "$DARM" =~ ^[a-z_]{1,32}$ ]] || { echo "bad arm name"; exit 1; }
    case "$DTYPE" in
      kill9)
        echo "drill: kill -9 ants@$DARM, then wait for recovery"
        "${SSH[@]}" "set -e
          PID=\$(systemctl show ants@$DARM -p MainPID --value)
          [ \"\$PID\" -gt 0 ] 2>/dev/null || { echo 'service not running'; exit 1; }
          sudo -n kill -9 \"\$PID\"
          echo 'killed pid '\$PID
          T0=\$(date +%s)
          for i in \$(seq 1 120); do
            sleep 1
            if systemctl is-active ants@$DARM >/dev/null 2>&1; then
              T1=\$(date +%s)
              DT=\$((T1 - T0))
              CHAIN=\$(sudo -n -u ants /opt/ants/.venv/bin/ants status --contract $CT --workdir /var/lib/ants/$DARM --arm $DARM 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(\"ok\" if d.get(\"chain_ok\") else \"BROKEN\")' 2>/dev/null || echo '?')
              LINE=\"\$(date -uIs) drill=kill9 arm=$DARM seconds=\$DT chain=\$CHAIN\"
              echo \"\$LINE\" | sudo -n tee -a /var/lib/ants/DRILLS.log >/dev/null
              echo \"\$LINE\"
              exit 0
            fi
          done
          echo 'TIMEOUT: service did not restart within 120 s'
          exit 1" ;;
      *) echo "unknown drill type: $DTYPE (available: kill9; see docs/operations.md for manual drills)"; exit 1 ;;
    esac ;;
  series-start)
    SNAME="${2:?series name required}"
    [[ "$SNAME" =~ ^[a-z0-9_]{1,40}$ ]] || { echo "bad series name: $SNAME"; exit 1; }
    "${SSH[@]}" "sudo -n systemctl enable --now ants-series@$SNAME.service ants-series-backup@$SNAME.timer && systemctl is-active ants-series@$SNAME" ;;
  series-stop)
    SNAME="${2:?series name required}"
    [[ "$SNAME" =~ ^[a-z0-9_]{1,40}$ ]] || { echo "bad series name: $SNAME"; exit 1; }
    "${SSH[@]}" "sudo -n systemctl disable --now ants-series@$SNAME.service ants-series-backup@$SNAME.timer" ;;
  series-status)
    SNAME="${2:?series name required}"
    [[ "$SNAME" =~ ^[a-z0-9_]{1,40}$ ]] || { echo "bad series name: $SNAME"; exit 1; }
    "${SSH[@]}" "systemctl status ants-series@$SNAME --no-pager; echo '---'; sudo -n -u ants /opt/ants/.venv/bin/ants series status $SNAME" ;;
  verify)
    # Read-only: run `ants preflight --json` on the server for the given arms/series, print a table, exit non-zero on any FAIL.
    shift
    VARGS=(); EXPECT_VER=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --series) shift; SNAME="$1"; [[ "$SNAME" =~ ^[a-z0-9_]{1,40}$ ]] || { echo "bad series name: $SNAME"; exit 1; }; VARGS+=("--series" "$SNAME"); shift ;;
        --expect-version) shift; EXPECT_VER="$1"; [[ "$EXPECT_VER" =~ ^[a-z0-9_.-]{1,40}$ ]] || { echo "bad version: $EXPECT_VER"; exit 1; }; shift ;;
        *) [[ "$1" =~ ^[a-z0-9_.-]{1,40}$ ]] || { echo "bad argument: $1"; exit 1; }; VARGS+=("$1"); shift ;;
      esac
    done
    REMOTE_CMD="sudo -n -u ants /opt/ants/.venv/bin/ants preflight --json ${VARGS[*]+"${VARGS[*]}"}"
    RAW=$("${SSH[@]}" "$REMOTE_CMD") || true
    if ! echo "$RAW" | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
      echo "preflight did not return valid JSON:"; echo "$RAW"; exit 1
    fi
    # print table and check for FAIL
    HAS_FAIL=$(echo "$RAW" | python3 -c "
import json, sys
rows = json.load(sys.stdin)
fail = False
for r in rows:
    tag = r['level']
    if tag == 'FAIL': fail = True
    print(f'[{tag:>4}] {r[\"message\"]}')
sys.exit(1 if fail else 0)
") && RC=0 || RC=1
    echo "$HAS_FAIL"
    if [ -n "$EXPECT_VER" ]; then
      LAST=$("${SSH[@]}" "tail -1 /var/lib/ants/DEPLOY.log 2>/dev/null || true")
      if ! echo "$LAST" | grep -qF "$EXPECT_VER"; then
        echo "FAIL: DEPLOY.log last line does not contain $EXPECT_VER: $LAST"; RC=1
      else
        echo "[  OK] DEPLOY.log contains $EXPECT_VER"
      fi
    fi
    exit $RC ;;
  *) echo "unknown command"; exit 1 ;;
esac

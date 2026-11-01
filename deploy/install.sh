#!/usr/bin/env bash
# Idempotent installer for Ubuntu 24.04. Run as root. Does NOT start anything and does NOT touch sshd, the firewall or passwords.
set -euo pipefail
umask 022
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
SRC="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
# rsync --delete below makes /opt/ants an exact copy of SRC: refuse anything that does not look like this project.
[ -f "$SRC/pyproject.toml" ] && [ -d "$SRC/src/antslab" ] && [ -f "$SRC/deploy/ants@.service" ] || { echo "$SRC is not an ants source tree: refusing"; exit 1; }
# Serialize deployments and refuse replacing files used by scientific services.
exec 9>/run/lock/ants-deploy.lock
flock -n 9 || { echo "another ants deployment holds the lock: refusing"; exit 75; }
ACTIVE=$(systemctl list-units 'ants@*' 'ants-series@*' --type=service --state=active,activating,reloading,deactivating --no-legend --plain)
[ -z "$ACTIVE" ] || { echo "scientific services still active: stop them gracefully before deploying"; echo "$ACTIVE"; exit 75; }
VERSION=$(python3 -c 'import sys,tomllib;print(tomllib.load(open(sys.argv[1],"rb"))["project"]["version"])' "$SRC/pyproject.toml")
HAD_CONFIG=0
[ ! -f /var/lib/ants/.ants/config.toml ] || HAD_CONFIG=1
OLD_EPOCH=""
if [ -x /opt/ants/.venv/bin/python ] && [ -f /opt/ants/contracts/tinyts_real.toml ]; then
  OLD_EPOCH=$(/opt/ants/.venv/bin/python -c 'from antslab.series import compute_epoch; print(compute_epoch("/opt/ants/contracts/tinyts_real.toml"))' 2>/dev/null || true)
fi
# The gate and wrapper live outside the tree being replaced and use system Python.
install -d -m 755 /usr/local/lib/ants /var/lib/ants-deploy
install -m 644 "$SRC/deploy/ants-deploy-gate.py" /usr/local/lib/ants/deploy_gate.py
install -m 755 "$SRC/deploy/ants-wrapper.sh" /usr/local/bin/ants
GATE=/usr/local/lib/ants/deploy_gate.py
READY=0
deployment_exit() {
  rc=$?
  if [ "$READY" != 1 ]; then
    python3 "$GATE" set failed --version "$VERSION" --reason "Installer exited before readiness checks completed (exit $rc). Inspect the deployment log before retrying." || true
  fi
}
trap deployment_exit EXIT
python3 "$GATE" set preparing --version "$VERSION"
echo "== preflight"; nproc; free -m | sed -n 2p; df -h /var | tail -1
FREE_GB=$(df -BG --output=avail /var | tail -1 | tr -dc 0-9); [ "$FREE_GB" -ge 15 ] || { echo "less than 15 GB free: refusing"; exit 1; }
echo "== user and directories"
id ants >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/ants --shell /usr/sbin/nologin ants
install -d -o ants -g ants -m 750 /var/lib/ants /var/lib/ants/archive /var/lib/ants/board /var/lib/ants/bundles /var/lib/ants/series
install -d -o root -g ants -m 750 /etc/ants
echo "== code"
python3 "$GATE" set installing --version "$VERSION"
apt-get install -y --no-install-recommends python3.12-venv rsync libgomp1 >/dev/null
install -d -o ants -g ants /opt/ants
# --delete keeps the code tree exact, but data, installed contracts and runs are the server's own state: never touched.
# data/MANIFEST.sha256 is NOT state: it is the pinned list of expected hashes shipped with the code, and `ants data verify` needs it.
rsync -a --delete --exclude .venv --exclude internal --exclude .git --exclude runs --exclude '*.sqlite*' --exclude '*.env' \
  --exclude 'data/raw' --exclude 'data/processed' --exclude 'data/MANIFEST.dev.sha256' --exclude 'contracts/tinyts.toml' --exclude build "$SRC"/ /opt/ants/
chown -R ants:ants /opt/ants
# The venv's own pip is used as shipped; everything else comes from the hashed lock (a mismatching hash aborts the install).
[ -x /opt/ants/.venv/bin/python ] || sudo -u ants python3 -m venv /opt/ants/.venv
sudo -u ants /opt/ants/.venv/bin/pip install -q --require-hashes -r /opt/ants/requirements/server.txt
sudo -u ants /opt/ants/.venv/bin/pip install -q --no-deps -e /opt/ants
echo "== units"
for u in ants@.service ants-janitor@.service ants-janitor@.timer ants-health@.service ants-health@.timer ants-backup@.service ants-backup@.timer ants-board.service ants-series@.service ants-series-backup@.service ants-series-backup@.timer; do
  install -m 644 "/opt/ants/deploy/$u" "/etc/systemd/system/$u"
done
systemctl daemon-reload
echo "== series registries"
if [ -d "$SRC/deploy/series" ]; then
  install -d -o ants -g ants /opt/ants/deploy/series
  for f in "$SRC"/deploy/series/*.toml; do
    [ -e "$f" ] || continue
    install -m 644 -o ants -g ants "$f" /opt/ants/deploy/series/
  done
fi
# `ants` on the PATH: runs as the service user (see the file), so `ssh server`, then `ants`, opens the interface
install -m 755 /opt/ants/deploy/ants-wrapper.sh /usr/local/bin/ants
echo "== secrets file check"
if [ -e /etc/ants/llm.env ]; then
  [ "$(stat -c '%a %U' /etc/ants/llm.env)" = "600 ants" ] || echo "WARNING: /etc/ants/llm.env must be mode 600, owner ants (chown ants:ants; chmod 600): the client refuses anything else"
fi
echo "== swap (safety buffer, not working memory)"
swapon --show | grep -q . || echo "no swap configured: see deploy/PREFLIGHT.md"
echo "== readiness"
python3 "$GATE" set verifying --version "$VERSION"
sudo -u ants /opt/ants/.venv/bin/python - "$VERSION" "$HAD_CONFIG" "$OLD_EPOCH" <<'PY'
import sys
from importlib.metadata import version
from antslab.cli import _first_run_done, _contract_path
from antslab.series import compute_epoch
import antslab.tui.app
import antslab.search_map
assert version('ants-lab') == sys.argv[1], 'installed version mismatch'
if sys.argv[2]=='1':assert _first_run_done(), 'existing setup was not rediscovered'
if sys.argv[3]:assert compute_epoch(_contract_path('/opt/ants/contracts/tinyts_real.toml')) == sys.argv[3], 'evaluator epoch changed; review required'
print('Installed imports, version, state discovery and evaluator identity verified')
PY
python3 "$GATE" set ready --version "$VERSION"
READY=1
echo "== done. Next: put keys in /etc/ants/llm.env (install -m 600 -o ants -g ants), copy a contract to /opt/ants/contracts/tinyts.toml,"
echo "   run the test suite, then: deploy/remote_deploy.sh start portfolio   (check: systemd-analyze security ants@portfolio)"

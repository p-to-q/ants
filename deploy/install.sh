#!/usr/bin/env bash
# Idempotent installer for Ubuntu 24.04. Run as root. Does NOT start anything and does NOT touch sshd, the firewall or passwords.
set -euo pipefail
umask 022
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
SRC="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
# rsync --delete below makes /opt/ants an exact copy of SRC: refuse anything that does not look like this project.
[ -f "$SRC/pyproject.toml" ] && [ -d "$SRC/src/antslab" ] && [ -f "$SRC/deploy/ants@.service" ] || { echo "$SRC is not an ants source tree: refusing"; exit 1; }
echo "== preflight"; nproc; free -m | sed -n 2p; df -h /var | tail -1
FREE_GB=$(df -BG --output=avail /var | tail -1 | tr -dc 0-9); [ "$FREE_GB" -ge 15 ] || { echo "less than 15 GB free: refusing"; exit 1; }
echo "== user and directories"
id ants >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/ants --shell /usr/sbin/nologin ants
install -d -o ants -g ants -m 750 /var/lib/ants /var/lib/ants/archive /var/lib/ants/board
install -d -o root -g ants -m 750 /etc/ants
echo "== code"
apt-get install -y --no-install-recommends python3.12-venv rsync libgomp1 >/dev/null
install -d -o ants -g ants /opt/ants
# --delete keeps the code tree exact, but data, installed contracts and runs are the server's own state: never touched
rsync -a --delete --exclude .venv --exclude internal --exclude .git --exclude runs --exclude '*.sqlite*' --exclude '*.env' \
  --exclude 'data/raw' --exclude 'data/processed' --exclude 'data/MANIFEST*' --exclude 'contracts/tinyts.toml' --exclude build "$SRC"/ /opt/ants/
chown -R ants:ants /opt/ants
# The venv's own pip is used as shipped; everything else comes from the hashed lock (a mismatching hash aborts the install).
[ -x /opt/ants/.venv/bin/python ] || sudo -u ants python3 -m venv /opt/ants/.venv
sudo -u ants /opt/ants/.venv/bin/pip install -q --require-hashes -r /opt/ants/requirements/server.txt
sudo -u ants /opt/ants/.venv/bin/pip install -q --no-deps -e /opt/ants
echo "== units"
for u in ants@.service ants-janitor.service ants-janitor.timer ants-health.service ants-health.timer ants-board.service; do
  install -m 644 "/opt/ants/deploy/$u" "/etc/systemd/system/$u"
done
systemctl daemon-reload
echo "== secrets file check"
if [ -e /etc/ants/llm.env ]; then
  [ "$(stat -c '%a %U' /etc/ants/llm.env)" = "600 ants" ] || echo "WARNING: /etc/ants/llm.env must be mode 600, owner ants (chown ants:ants; chmod 600): the client refuses anything else"
fi
echo "== swap (safety buffer, not working memory)"
swapon --show | grep -q . || echo "no swap configured: see deploy/PREFLIGHT.md"
echo "== done. Next: put keys in /etc/ants/llm.env (install -m 600 -o ants -g ants), copy a contract to /opt/ants/contracts/tinyts.toml,"
echo "   run the test suite, then: systemctl enable --now ants@portfolio ants-janitor.timer ants-health.timer   (check: systemd-analyze security ants@portfolio)"

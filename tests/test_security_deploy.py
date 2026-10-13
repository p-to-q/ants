"""Packaging, secret scanning and deployment files. Fake secrets are assembled at run time so this file itself stays clean."""
import configparser
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
SCAN = ROOT / "scripts/scan_secrets.py"


def j(*parts):
    return "".join(parts)


FAKES = {                                  # name of the rule that must fire -> a line that contains such a secret
    "aws-access-key-id": j("aws_key_id = ", "AKIA", "QYLPMN5HHFJZ7XQD"),
    "tencent-secret-id": j("id = ", "AKID", "z8mSfMEXAMPLEq2P4N7kLw0aBcDeF1gHiJk"),
    "github-token": j("token_here ", "ghp", "_", "R2d9Xk3LmQ8vN1sT5yU7wZ0aB4cE6gH8jK2n"),
    "github-fine-grained-token": j("x = ", "github", "_pat_", "11AFGHJK0abcdefGHIJKL_9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1"),
    "openai-or-anthropic-style-key": j("OPENAI = ", "sk", "-", "proj-", "Zx9Qw3Er7Ty1Ui5Op8As2Df6Gh0Jk4Lm"),
    "anthropic": j("k = ", "sk", "-ant-", "api03-", "Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9Jj0Kk1Ll2Mm3Nn4"),
    "slack-token": j("SLACK ", "xox", "b-", "123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx"),
    "jwt": j("auth ", "eyJ", "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", ".", "eyJ", "zdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ", ".", "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"),
    "private-key-block": j("-----BEGIN ", "OPENSSH PRIVATE KEY-----"),
    "private-key-block-rsa": j("-----BEGIN ", "RSA PRIVATE KEY-----"),
    "google-api-key": j("g = ", "AIza", "SyD3x7Qm9Pv2Lk5Jh8Gf1Ed4Cb6Ba0Zy9Xw"),
    "stripe-live-key": j("s = ", "sk", "_live_", "4eC39HqLyjWDarjtT1zdp7dc"),
    "llm-env-assignment": j("LLM_", "ABC", "_KEY", "=", "a8Zk3Qw9Xr2Lm5Pv7Ty"),
    "credentials-in-url": j("url = https://", "deploy:", "hunter2secret", "@git.example.org/repo"),
    "bearer-token": j("Authorization: Bearer ", "q8W3eR7tY1uI5oP9aS2dF6gH0jK4"),
    "email-address": j("contact ", "alice.wong", "@", "corp-mail.com"),
    "public-ipv4-address": j("host = ", "34.", "120.", "7.", "91"),
    "high-entropy": j("API_TOKEN = '", "q8W3eR7tY1uI5oP9aS2dF6gH0jK4lZ8xC3vB7nM1", "'"),
    "high-entropy-password": j('password: "', "Zx9Qw3Er7Ty1Ui5Op8As2Df6Gh0Jk4LmNb", '"'),
}
CLEAN_LINES = [
    "freeze_hash = 'ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12'",     # a hash is not a secret
    "headers = {'Authorization': 'Bearer ' + ep.key}", "LLM_<TAG>_KEY in the env file", "api_key = os.environ['ANTS_KEY']",
    "token = 'short'", "security: hi@ptoq.io", "Co-Authored-By: X <noreply@anthropic.com>", "git@github.com:org/repo.git", "systemctl status ants@portfolio.service",
    "bind 10.0.0.5, 127.0.0.1, 192.168.1.20, 172.16.3.4, 0.0.0.0", "docs use 192.0.2.1 and 203.0.113.9", "nvidia-cublas==12.1.3.1", "SECRET_FILE = '/etc/ants/llm.env'", "password_hint = ''", "key = 'a' * 40",
    "sk-learn is a library; sk-1 is not a key", "see https://example.com/docs and ssh://git@example.com/x.git",
    "KEY = '" + "x" * 40 + "'", "token = '" + "0" * 40 + "'",
    j("sk", "-live-", "abcdefghijklmnopqrstuvwxyz"),            # a documented fake: tested with the scan:allow marker below
]


def scanner():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import importlib
        return importlib.import_module("scan_secrets")
    finally:
        sys.path.pop(0)


@pytest.fixture(scope="module")
def S():
    if not SCAN.exists():
        pytest.fail("scripts/scan_secrets.py is missing")
    return scanner()


def test_every_kind_of_planted_fake_secret_is_detected(S):
    for name, line in FAKES.items():
        assert S.scan_bytes("f", (line + "\n").encode()), f"{name}: not detected"


def test_clean_lines_and_documented_examples_are_not_flagged(S):
    for line in CLEAN_LINES[:-1]:
        assert S.scan_bytes("f", (line + "\n").encode()) == [], line
    assert S.scan_bytes("f", (CLEAN_LINES[-1] + "\n").encode())                       # without the marker it is a finding...
    assert S.scan_bytes("f", (CLEAN_LINES[-1] + "  # scan:allow\n").encode()) == []    # ...with it, deliberately skipped


def test_findings_never_print_the_secret(S, tmp_path, capsys):
    (tmp_path / "a.py").write_text(FAKES["github-token"] + "\n")
    assert S.main(["x", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "github-token" in err and "R2d9Xk3L" not in err


def test_binary_gzip_and_zip_members_are_looked_into(S, tmp_path):
    import gzip
    import zipfile
    secret = FAKES["github-token"].encode()
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02" * 100 + secret + b"\xff\xfe")
    (tmp_path / "x.gz").write_bytes(gzip.compress(b"hello\n" + secret + b"\n"))
    with zipfile.ZipFile(tmp_path / "x.zip", "w") as z:
        z.writestr("inner.txt", secret)
    found = "\n".join(S.scan_tree(tmp_path))
    assert "blob.bin" in found and "x.gz!gz" in found and "x.zip!inner.txt" in found


def test_secret_bearing_file_names_and_escaping_links_are_refused(S, tmp_path):
    for n in (".env", ".env.production", "keys/server.pem", "id_ed25519", "a/id_rsa", "ledger.sqlite", "ledger.sqlite-wal", "internal/notes.md",
              ".git/config", "deploy/credentials.json", ".netrc", "x/._hidden", "tls.key", ".aws/credentials"):
        assert S.check_name(n), n
    for n in ("src/antslab/llm.py", "docs/modules/llm.md", "deploy/llm.env.example.txt", "README.md"):
        assert S.check_name(n) == [], n
    os.symlink("/etc/passwd", tmp_path / "link")
    assert any("symlink" in f for f in S.scan_tree(tmp_path))


def test_the_repository_itself_is_clean(S, tmp_path):
    """No false positives on our own tree (docs, tests with fake keys, deploy files): otherwise nobody could pack a release."""
    dst = tmp_path / "tree"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("data", ".venv", "runs", "internal", "dist", "__pycache__", ".pytest_cache",
                                                              ".ruff_cache", "*.egg-info", ".git", "*.sqlite*", "*.env"), symlinks=True)
    assert S.scan_tree(dst) == []


# --------------------------------------------------------------------------- scripts/pack_public.sh on a fixture tree
needs_tools = pytest.mark.skipif(not (shutil.which("rsync") and shutil.which("tar") and shutil.which("bash")), reason="needs rsync, tar, bash")


def fixture_tree(tmp_path):
    t = tmp_path / "proj"
    (t / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/pack_public.sh", t / "scripts/pack_public.sh")
    if SCAN.exists():
        shutil.copy(SCAN, t / "scripts/scan_secrets.py")
    (t / "src/antslab").mkdir(parents=True)
    (t / "src/antslab/a.py").write_text("x = 1\n")
    (t / "README.md").write_text("# demo\n")
    (t / "internal").mkdir()
    (t / "internal/notes.md").write_text(FAKES["github-token"] + "\n")           # excluded from the package, must not matter
    (t / "runs").mkdir()
    (t / "runs/x.env").write_text(FAKES["llm-env-assignment"] + "\n")
    (t / ".git").mkdir()
    (t / ".git/config").write_text("[core]\n")
    return t


def pack(t, **env):
    e = {**os.environ, **env}
    return subprocess.run(["bash", "scripts/pack_public.sh"], cwd=t, env=e, capture_output=True, text=True, timeout=120)


def members(t):
    tars = list((t / "dist").glob("antslab-*.tar.gz"))
    assert len(tars) == 1, list((t / "dist").iterdir())
    with tarfile.open(tars[0]) as f:
        return f.getnames()


@needs_tools
def test_clean_tree_packs_and_excludes_internal_git_runs_and_env(tmp_path):
    t = fixture_tree(tmp_path)
    r = pack(t)
    assert r.returncode == 0, r.stderr
    names = members(t)
    assert "antslab/src/antslab/a.py" in names and not any(re.search(r"internal|\.git|runs|\.env", n) for n in names)
    assert not list((t / "dist").glob("*.partial"))


@needs_tools
@pytest.mark.parametrize("name", sorted(FAKES))
def test_a_planted_secret_in_a_shipped_file_refuses_the_pack_and_leaves_no_tarball(tmp_path, name):
    t = fixture_tree(tmp_path)
    (t / "docs").mkdir()
    (t / "docs/guide.md").write_text("intro\n" + FAKES[name] + "\nmore\n")
    r = pack(t)
    assert r.returncode != 0 and "REFUSING" in r.stdout + r.stderr, (r.stdout, r.stderr)
    assert not list((t / "dist").glob("antslab-*")), "a tainted tarball was left in dist/"
    assert FAKES[name].split()[-1] not in r.stdout + r.stderr                      # the secret itself is not echoed


@needs_tools
def test_secrets_in_binary_files_and_secret_bearing_names_are_caught(tmp_path):
    for fname, content in (("model.bin", b"\x00\x01" * 50 + FAKES["aws-access-key-id"].encode() + b"\x02"),
                           ("server.p12", b"not really a keystore"), ("deploy_id_rsa/id_rsa", b"junk")):
        t = fixture_tree(tmp_path / fname.replace("/", "_"))
        (t / fname).parent.mkdir(parents=True, exist_ok=True)
        (t / fname).write_bytes(content)
        r = pack(t)
        assert r.returncode != 0 and not list((t / "dist").glob("antslab-*")), fname


@needs_tools
def test_a_symlink_that_escapes_the_package_is_refused(tmp_path):
    t = fixture_tree(tmp_path)
    os.symlink("/etc/passwd", t / "src/passwd_link")
    r = pack(t)
    assert r.returncode != 0 and not list((t / "dist").glob("antslab-*"))


@needs_tools
def test_the_final_tarball_is_scanned_even_if_the_staging_scan_missed_something(tmp_path):
    t = fixture_tree(tmp_path)
    (t / "src/antslab/leak.py").write_text(FAKES["github-token"] + "\n")
    shim = tmp_path / "py_shim.sh"                  # a scanner that waves the staging directory through (as a buggy or bypassed scan would)
    shim.write_text(f'#!/bin/sh\n[ -d "$3" ] && exit 0\nexec {sys.executable} "$@"\n')
    shim.chmod(0o755)
    r = pack(t, PYTHON=str(shim))
    assert r.returncode != 0 and "final tarball" in r.stdout + r.stderr
    assert not list((t / "dist").glob("antslab-*"))


def test_scanner_tar_mode_reads_what_is_really_inside(S, tmp_path):
    p = tmp_path / "pkg.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        for n, data in (("antslab/ok.py", b"x = 1\n"), ("antslab/.env", b"A=1\n"), ("antslab/leak.txt", FAKES["slack-token"].encode())):
            ti = tarfile.TarInfo(n)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        ti = tarfile.TarInfo("antslab/../../etc/cron.d/x")
        ti.size = 0
        tf.addfile(ti, io.BytesIO(b""))
    found = "\n".join(S.scan_tar(p))
    assert ".env" in found and "slack-token" in found and "parent-relative" in found


# --------------------------------------------------------------------------- systemd units and shell scripts
UNITS = ["ants@.service", "ants-board.service", "ants-janitor@.service", "ants-health@.service", "ants-backup@.service"]
KNOWN = set("""Description Wants After StartLimitIntervalSec StartLimitBurst Type User Group WorkingDirectory ExecStart Restart RestartSec RestartPreventExitStatus TimeoutStopSec
KillMode CPUQuota MemoryHigh MemoryMax OOMPolicy OOMScoreAdjust TasksMax LimitFSIZE Environment NoNewPrivileges CapabilityBoundingSet
AmbientCapabilities PrivateTmp PrivateDevices ProtectSystem ProtectHome ReadWritePaths InaccessiblePaths ProtectKernelTunables
ProtectKernelModules ProtectKernelLogs ProtectControlGroups ProtectClock ProtectHostname ProtectProc RestrictAddressFamilies RestrictNamespaces
RestrictRealtime RestrictSUIDSGID LockPersonality SystemCallArchitectures UMask IPAddressDeny IPAddressAllow WantedBy OnBootSec OnUnitActiveSec
Persistent NotifyAccess WatchdogSec Nice IOSchedulingClass OnCalendar RandomizedDelaySec""".split())


def parse(name):
    cp = configparser.ConfigParser(strict=False, interpolation=None, delimiters=("=",))
    cp.optionxform = str
    cp.read_string((DEPLOY / name).read_text())
    return cp


def test_unit_files_referenced_by_the_installer_and_remote_deploy_exist():
    install = (DEPLOY / "install.sh").read_text() + (DEPLOY / "remote_deploy.sh").read_text() + (DEPLOY / "PREFLIGHT.md").read_text()
    wanted = set(re.findall(r"\b(ants(?:-[a-z]+)?(?:@\.service|@\.timer|\.service|\.timer))", install))
    assert wanted >= {"ants@.service", "ants-janitor@.service", "ants-janitor@.timer", "ants-board.service", "ants-health@.service", "ants-health@.timer", "ants-backup@.service", "ants-backup@.timer"}
    for w in wanted:
        assert (DEPLOY / w).exists(), f"{w} is installed by install.sh but does not exist in deploy/"
    assert all(p.name.startswith(("ants", "install", "remote_deploy", "PREFLIGHT")) for p in DEPLOY.iterdir()), "deploy files are named ants-*"


@pytest.mark.parametrize("unit", UNITS)
def test_every_directive_is_a_known_systemd_directive(unit):
    """systemd ignores unknown keys with a warning: a typo in a hardening directive would silently leave the service unprotected."""
    cp = parse(unit)
    for sec in cp.sections():
        for key in cp[sec]:
            assert key in KNOWN, f"{unit}: {key} is not in the reviewed directive list"


@pytest.mark.parametrize("unit", UNITS)
def test_units_carry_the_hardening_set_and_nothing_that_breaks_numpy(unit):
    s = parse(unit)["Service"]
    must = {"NoNewPrivileges": "yes", "ProtectSystem": "strict", "ProtectHome": "yes", "PrivateTmp": "yes", "PrivateDevices": "yes",
            "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes", "ProtectControlGroups": "yes", "RestrictNamespaces": "yes",
            "LockPersonality": "yes", "SystemCallArchitectures": "native", "CapabilityBoundingSet": "", "UMask": "0077",
            "ReadWritePaths": "/var/lib/ants", "User": "ants"}
    for k, v in must.items():
        assert s.get(k) == v, f"{unit}: {k} should be {v!r}, is {s.get(k)!r}"
    assert s["RestrictAddressFamilies"] in ("AF_UNIX AF_INET AF_INET6", "AF_UNIX")
    text = (DEPLOY / unit).read_text()
    assert not re.search(r"^\s*MemoryDenyWriteExecute\s*=\s*(yes|true|1)", text, re.M)       # numpy / onnxruntime need W+X mappings
    assert not re.search(r"^\s*SystemCallFilter\s*=", text, re.M)                              # untestable here; see the comment in the unit
    assert "AF_PACKET" not in text and "AF_NETLINK" not in text


def test_secrets_never_reach_the_process_environment_or_the_command_line():
    for unit in UNITS:
        text = (DEPLOY / unit).read_text()
        assert not re.search(r"^\s*EnvironmentFile", text, re.M), unit
        for line in text.splitlines():
            if line.startswith("ExecStart"):
                assert not re.search(r"KEY=|TOKEN|SECRET|PASSW|sk-", line), line
    assert "--llm-env /etc/ants/llm.env" in (DEPLOY / "ants@.service").read_text()


def test_the_board_unit_is_loopback_only_and_has_no_secrets_dir():
    s = parse("ants-board.service")["Service"]
    assert s["IPAddressDeny"] == "any" and s["IPAddressAllow"] == "localhost" and s["InaccessiblePaths"] == "/etc/ants"
    assert "0.0.0.0" not in (DEPLOY / "ants-board.service").read_text()


def test_no_per_file_size_limit_that_a_weeks_old_ledger_would_hit():
    for unit in UNITS:
        assert not re.search(r"^\s*LimitFSIZE\s*=", (DEPLOY / unit).read_text(), re.M), unit
    assert "LimitFSIZE" in (DEPLOY / "ants@.service").read_text()                     # the explanation stays next to the missing line


def test_the_campaign_restarts_forever_and_a_health_timer_runs_doctor():
    s = parse("ants@.service")
    assert s["Service"]["Restart"] == "always" and s["Unit"]["StartLimitIntervalSec"] == "0" and 0 < int(s["Service"]["RestartSec"].rstrip("s")) <= 60
    h = parse("ants-health@.service")["Service"]
    assert " doctor " in h["ExecStart"] and h["Type"] == "oneshot" and h["IPAddressDeny"] == "any"
    t = parse("ants-health@.timer")["Timer"]
    assert t["OnUnitActiveSec"].endswith("min") and "ants-health@.timer" in (DEPLOY / "install.sh").read_text()


@pytest.mark.parametrize("script", [DEPLOY / "install.sh", DEPLOY / "remote_deploy.sh", ROOT / "scripts/pack_public.sh"])
def test_shell_scripts_parse_and_use_strict_mode(script):
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0
    assert "set -euo pipefail" in script.read_text()


def test_installer_does_not_touch_sshd_firewall_or_trust_unpinned_downloads():
    text = (DEPLOY / "install.sh").read_text()
    code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#") and not l.strip().startswith("echo"))
    assert not re.search(r"sshd|ufw|iptables|nft |passwd|authorized_keys|chmod\s+(-R\s+)?[0-7]*[2367][0-7]?[0-7]?\s", code)
    assert "curl" not in code and "wget" not in code and "--upgrade pip" not in code
    assert "--require-hashes" in code and "-f \"$SRC/pyproject.toml\"" in code            # hashed lock; refuses a foreign source tree


def test_remote_deploy_stages_the_root_installer_privately_and_verifies_its_hash():
    text = (DEPLOY / "remote_deploy.sh").read_text()
    assert "/tmp/ants" not in text and "/tmp/antslab" not in text                       # no predictable path in a world-writable directory
    assert "mkdir -m 700" in text and "sha256sum -c" in text and "StrictHostKeyChecking=yes" in text
    assert re.search(r'\[\[ "\$N" =~ \^\[0-9\]', text)                                 # smoke N is interpolated into a remote shell line


def test_remote_deploy_has_no_built_in_host_user_or_key():
    text = (DEPLOY / "remote_deploy.sh").read_text()
    for var in ("ANTS_HOST", "ANTS_USER", "ANTS_KEY"):
        assert re.search(r"\$\{%s:\?" % var, text), f"{var} must be required, not defaulted"
    assert "ubuntu" not in text.replace("<user>", "") and "ed25519" not in text and not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", text)


def test_installer_ships_the_pinned_manifest_but_keeps_the_servers_own_one():
    text = (DEPLOY / "install.sh").read_text()
    rsync = text[text.index("rsync -a --delete"):text.index("chown -R")]
    assert "data/MANIFEST.sha256" not in rsync and "MANIFEST*" not in rsync     # the pinned manifest is copied
    assert "data/MANIFEST.dev.sha256" in rsync                                  # the one built on the server survives


def test_installer_protects_server_state_from_rsync_delete():
    text = (DEPLOY / "install.sh").read_text()
    rsync = text[text.index("rsync -a --delete"):text.index("chown -R")]
    for keep in ("data/raw", "data/processed", "data/MANIFEST.dev.sha256", "contracts/tinyts.toml", "runs", "*.sqlite*", "*.env", ".venv"):
        assert f"--exclude '{keep}'" in rsync or f"--exclude {keep}" in rsync, keep


def test_the_campaign_unit_is_notify_with_a_watchdog_and_the_backup_unit_runs_a_verified_backup():
    m = parse("ants@.service")["Service"]
    assert m["Type"] == "notify" and m["NotifyAccess"] == "main" and 120 <= int(m["WatchdogSec"]) <= 900
    b = parse("ants-backup@.service")["Service"]
    assert " backup " in b["ExecStart"] and "--dest /var/lib/ants/backups/%i" in b["ExecStart"] and b["Type"] == "oneshot"
    assert parse("ants-backup@.timer")["Timer"]["Persistent"] == "true"
    h = parse("ants-health@.service")["Service"]
    assert h["Environment"] == "ANTS_BACKUP_DIR=/var/lib/ants/backups/%i"          # doctor looks where the backup unit writes


def test_three_instances_fit_the_two_gigabyte_two_core_host():
    m = parse("ants@.service")["Service"]
    mem = lambda v: int(v.rstrip("M"))
    assert 3 * mem(m["MemoryMax"]) <= 1990 and 3 * int(m["CPUQuota"].rstrip("%")) <= 200

"""The commands that make ants a resident system rather than a script: onboard, service, backup/restore, sql, and the durability
checks that `ants doctor` adds. Each takes the argparse namespace from cli.py (with `workdir` already resolved where it needs one).
Standard library only.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import service as svc
from .backup import BackupError, list_backups, make_backup, restore_backup
from .home import data_dir, home, load_config, package_root, save_config
from .ledger import Ledger
from .sqlview import SqlRefused, run_query, schema

NETWORK_FS = ("nfs", "nfs4", "cifs", "smb", "smbfs", "smb3", "fuse.sshfs", "9p", "afs", "ceph")
BACKUP_STALE_H = 36
DATA_FILES = ("catalog.toml", "catalog_synth.toml", "sources.toml", "MANIFEST.sha256")


def backup_dir(workdir) -> Path:
    cfg = load_config().get("backup", {})
    wd = Path(workdir).expanduser().resolve()
    tag = f"{wd.name}-{hashlib.sha256(str(wd).encode()).hexdigest()[:6]}"          # two workdirs both called `default` must not share backups
    if not (os.environ.get("ANTS_BACKUP_DIR") or cfg.get("dest")) and ((wd.parent / "backups" / wd.name).is_dir() or str(wd.parent) == "/var/lib/ants"):
        return wd.parent / "backups" / wd.name                       # the server layout: /var/lib/ants/backups/<arm> next to /var/lib/ants/<arm>
    return Path(os.environ.get("ANTS_BACKUP_DIR") or cfg.get("dest") or home() / "backups" / tag).expanduser()


# ------------------------------------------------------------------------------------------------------------ doctor
def durability_checks(led, workdir) -> list[tuple[str, str]]:
    """(level, message) lines about the ledger file itself, appended to `ants doctor`"""
    out = []
    info = led.check(full=False)
    out.append(("OK" if info["integrity_ok"] else "FAIL", f"sqlite integrity: {info['integrity']}"))
    out.append(("OK", f"schema version {info['schema_version']}, ledger {info['bytes'] / 1e6:.1f} MB, {info['trials']} trials, {info['events']} events"))
    if info["wal_bytes"] > 256 << 20:
        out.append(("WARN", f"the write-ahead log is {info['wal_bytes'] >> 20} MB: checkpoints are not completing (a reader holds the file open?)"))
    if info["fs"] in NETWORK_FS:
        out.append(("FAIL", f"the ledger is on a network filesystem ({info['fs']}): SQLite's WAL locking is not safe there; use a local disk"))
    dest = backup_dir(workdir)
    bks = list_backups(dest) if dest.exists() else []
    if not bks:
        out.append(("WARN", f"no backup yet (run `ants backup`, or `ants service install` for a daily one); expected in {dest}"))
    else:
        age = (time.time() - bks[0]["created"]) / 3600
        out.append(("OK" if age <= BACKUP_STALE_H else "WARN", f"newest backup {age:.1f} h old ({bks[0]['file']}, {len(bks)} kept)"))
    return out


# ------------------------------------------------------------------------------------------------------------ backup
def cmd_backup(a) -> int:
    dest = Path(a.dest).expanduser() if a.dest else backup_dir(a.workdir)
    try:
        meta = make_backup(Path(a.workdir) / "ledger.sqlite", dest, keep=a.keep, compress=a.gzip)
    except BackupError as e:
        print(f"ants backup: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(meta, indent=1))
    else:
        print(f"backed up {meta['trials']} trials / {meta['events']} events (chain head {meta['head'][:16]}) -> {dest / meta['file']}")
        print(f"copy {dest} off this machine: an anchor kept next to the ledger cannot expose a rewritten ledger")
    return 0


def cmd_backups(a) -> int:
    dest = Path(a.dest).expanduser() if a.dest else backup_dir(a.workdir)
    rows = list_backups(dest)
    if a.json:
        print(json.dumps(rows, indent=1))
    else:
        for m in rows:
            print(f"{m['file']:<40} {time.strftime('%Y-%m-%d %H:%M', time.localtime(m['created']))}  {m['stored_bytes'] / 1e6:7.1f} MB  "
                  f"{m['trials']} trials  head {m['head'][:12]}" + ("" if m["present"] else "  MISSING"))
        if not rows:
            print(f"no backups in {dest}")
    return 0


def cmd_restore(a) -> int:
    from .campaign import AlreadyRunning, InstanceLock
    dest = Path(a.dest).expanduser() if a.dest else backup_dir(a.workdir)
    lock = InstanceLock(a.workdir)

    def stopped():
        try:
            lock.acquire()
        except AlreadyRunning as e:
            raise BackupError(f"{e}; stop the service first") from None
    try:
        res = restore_backup(dest, a.which, Path(a.workdir) / "ledger.sqlite", force=a.force, lock_check=stopped, allow_different=a.allow_different)
    except BackupError as e:
        print(f"ants restore: {e}", file=sys.stderr)
        return 2
    finally:
        lock.release()
    led = Ledger(Path(a.workdir) / "ledger.sqlite")
    led.event("restored", {"backup": res["restored"], "seq": res["seq"], "head": res["head"], "replaced": res["moved_aside"]})      # the chain records that history was swapped
    led.close()
    print(f"restored {res['restored']} -> {res['to']} (seq {res['seq']}, head {res['head'][:16]})")
    if res["moved_aside"]:
        print(f"the previous ledger was kept as {res['moved_aside']}")
    return 0


# ------------------------------------------------------------------------------------------------------------ sql
def format_rows(cols: list[str], rows: list[list], fmt: str) -> str:
    if fmt == "json":
        return json.dumps([dict(zip(cols, r)) for r in rows], indent=1, default=str)
    if fmt == "jsonl":
        return "\n".join(json.dumps(dict(zip(cols, r)), default=str) for r in rows)
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(cols)
        from .cli import _cell
        for r in rows:
            w.writerow([_cell(x) for x in r])
        return buf.getvalue().rstrip("\n")
    cells = [[("" if v is None else (f"{v:.6g}" if isinstance(v, float) else str(v)))[:60] for v in r] for r in rows]
    width = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(cols)]
    line = lambda r: "  ".join(x.ljust(w) for x, w in zip(r, width)).rstrip()
    return "\n".join([line(cols), line(["-" * w for w in width]), *(line(r) for r in cells)])


def cmd_sql(a) -> int:
    db = Path(a.workdir) / "ledger.sqlite"
    try:
        if a.schema or not a.sql:
            print(schema(db))
            return 0
        cols, rows = run_query(db, a.sql, a.limit)
    except (SqlRefused, FileNotFoundError) as e:
        print(f"ants sql: {e}", file=sys.stderr)
        return 2
    print(format_rows(cols, rows, a.format))
    return 0


# ------------------------------------------------------------------------------------------------------------ service
def _env_for_service() -> dict[str, str]:
    env = {"ANTS_HOME": str(home())}
    if os.environ.get("ANTS_ROOT"):
        env["ANTS_ROOT"] = os.environ["ANTS_ROOT"]
    return env


def _sh(cmd: list[str]) -> int:
    return subprocess.run(cmd, check=False).returncode


def cmd_service(a) -> int:
    name = a.name
    unit = svc.unit_name(name)
    mac = sys.platform == "darwin"
    if a.action == "install":
        argv = svc.command(svc.run_args(a.contract, a.workdir, a.arm, a.seed, a.llm_env))
        env = _env_for_service()
        if mac:
            logdir = home() / "logs"
            files = {svc.launchd_dir() / f"io.ptoq.{unit}.plist": svc.render_plist(name, argv, env, logdir),
                     svc.launchd_dir() / f"io.ptoq.{unit}-backup.plist": svc.render_backup_plist(name, svc.command(["backup", "--workdir", a.workdir]), env, logdir)}
        else:
            bsvc, btimer = svc.render_backup_units(name, svc.command(["backup", "--workdir", a.workdir]), env)
            d = svc.systemd_dir(True)
            files = {d / f"{unit}.service": svc.render_systemd(name, argv, env).encode(),
                     d / f"{unit}-backup.service": bsvc.encode(), d / f"{unit}-backup.timer": btimer.encode()}
        if a.print:
            for p, body in files.items():
                print(f"# {p}\n{body.decode() if isinstance(body, bytes) else body}")
            return 0
        (home() / "logs").mkdir(parents=True, exist_ok=True)
        for p, body in files.items():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_bytes(body)
            tmp.replace(p)
            print(f"wrote {p}")
        if mac:
            for plist in files:
                _sh(["launchctl", "unload", str(plist)])       # launchd rereads a plist only after an unload
            if a.now:
                return max(_sh(["launchctl", "load", "-w", str(p)]) for p in files)
            print("start it with: " + "; ".join(f"launchctl load -w {p}" for p in files))
            return 0
        _sh(["systemctl", "--user", "daemon-reload"])
        if a.now:
            rc = _sh(["systemctl", "--user", "enable", "--now", f"{unit}.service", f"{unit}-backup.timer"])
            print("keep it running after you log out:  loginctl enable-linger $USER")
            return rc
        print(f"start it with: systemctl --user enable --now {unit}.service {unit}-backup.timer")
        return 0
    if a.action == "uninstall":
        if mac:
            for plist in (svc.launchd_dir() / f"io.ptoq.{unit}.plist", svc.launchd_dir() / f"io.ptoq.{unit}-backup.plist"):
                _sh(["launchctl", "unload", str(plist)])
                plist.unlink(missing_ok=True)
        else:
            _sh(["systemctl", "--user", "disable", "--now", f"{unit}.service", f"{unit}-backup.timer"])
            for n in (f"{unit}.service", f"{unit}-backup.service", f"{unit}-backup.timer"):
                (svc.systemd_dir(True) / n).unlink(missing_ok=True)
            _sh(["systemctl", "--user", "daemon-reload"])
        print(f"removed {unit}; the ledger and backups were left alone")
        return 0
    if a.action == "restart":
        return _sh(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/io.ptoq.{unit}"] if mac else ["systemctl", "--user", "restart", f"{unit}.service"])
    if a.action == "status":
        return _sh(["launchctl", "list", f"io.ptoq.{unit}"] if mac else ["systemctl", "--user", "status", "--no-pager", f"{unit}.service"])
    if a.action == "logs":
        if mac:
            log = home() / "logs" / f"{unit}.log"
            return _sh(["tail", "-n", str(a.lines), *(["-f"] if a.follow else []), str(log)])
        return _sh(["journalctl", "--user", "-u", f"{unit}.service", "-n", str(a.lines), "--no-pager", *(["-f"] if a.follow else [])])
    return 1


# ------------------------------------------------------------------------------------------------------------ onboard
def stage_data(root: Path | None, target: Path, log=print) -> list[str]:
    """copy the catalog, sources and pinned manifest shipped with this version into <home>/data (never touches raw/ or processed/)"""
    copied = []
    target.mkdir(parents=True, exist_ok=True)
    src = (root / "data") if root else None
    for n in DATA_FILES:
        if src and (src / n).exists():
            if not (target / n).exists() or (target / n).read_bytes() != (src / n).read_bytes():
                shutil.copyfile(src / n, target / n)
                copied.append(n)
    return copied


def cmd_onboard(a) -> int:
    from .fetch import TIERS, fetch
    h = home()
    try:
        h.mkdir(parents=True, exist_ok=True)
        for sub in ("runs", "backups", "logs"):
            (h / sub).mkdir(exist_ok=True)
    except OSError as e:
        print(f"ants onboard: cannot create {h}: {e}", file=sys.stderr)
        return 2
    root = package_root()
    copied = stage_data(root, data_dir())
    if not (data_dir() / "catalog.toml").exists():
        print("ants onboard: the data catalog is not part of this install; reinstall ants-lab or set ANTS_ROOT to a checkout", file=sys.stderr)
        return 2
    cfg = load_config()
    old = cfg.get("run", {})                                    # running `ants onboard` again must not undo an earlier choice
    contract = a.contract or "tinyts_real"
    cfg_contract = str(root / "contracts" / f"{contract}.toml") if root and (root / "contracts" / f"{contract}.toml").exists() else contract
    workdir = str(Path(a.workdir).expanduser().resolve()) if a.workdir else old.get("workdir") or str(h / "runs" / "default")
    cfg["run"] = {"contract": cfg_contract if a.contract or not old.get("contract") else old["contract"], "workdir": workdir,
                  "arm": a.arm or old.get("arm") or "portfolio"}
    cfg["data"] = {"tier": a.tier}
    p = save_config(cfg)
    print(f"home      {h}\nconfig    {p}\ndata      {data_dir()} ({len(copied)} file(s) updated)\nworkdir   {workdir}")
    rc = 0
    if not a.no_fetch:
        if a.tier not in TIERS:
            print(f"ants onboard: unknown tier {a.tier!r}", file=sys.stderr)
            return 2
        try:
            rep = fetch(data_dir(), a.tier)
        except OSError as e:
            print(f"ants onboard: {e}", file=sys.stderr)
            return 2
        print(f"datasets  tier {a.tier}: {len(rep['fetched'])} fetched, {len(rep['have'])} already here, {len(rep['failed'])} failed, {len(rep['mismatch'])} rejected")
        if rep["failed"] or rep["mismatch"]:
            rc = 2
            print("re-run `ants onboard` (it resumes) or `ants data fetch`; files that differ from the pinned hash were set aside as *.rejected")
        else:
            try:
                from .data import Catalog
                cat = Catalog(data_dir() / "catalog.toml")
                have = [n for n in cat.names() if cat.meta[n]["format"] != "synth" and (cat.base / cat.meta[n]["raw"]).exists()]
                cat.build_processed(have)                       # only what this tier downloaded; the synthetic suite is computed on demand
                print(f"processed {len(have)} dataset(s): {', '.join(have)}")
            except Exception as e:                              # a real-data contract needs them, but the loop itself can still be inspected
                print(f"processed (skipped): {type(e).__name__}: {e}", file=sys.stderr)
                rc = 2
    print("\nnext:\n  ants                    interactive shell (top, best, show, log, hint, doctor)\n"
          "  ants run                run the loop in the foreground\n"
          "  ants service install --now   keep it running, restart it if it dies, back it up daily")
    return rc

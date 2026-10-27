"""Versioned development candidates; never a champion registry or a weight checkpoint.

The detector is an explicit sidecar over a read-only ledger, not a viewer write
or a change to the campaign. Immutable records survive pointer replacement.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from .ledger import Ledger, canon

MAX_BYTES = 1 << 20
ID = re.compile(r"^[0-9a-f]{64}$")


def _digest(value):
    return hashlib.sha256(canon(value).encode()).hexdigest()


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _directory(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("candidate store directories must not be symlinks")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise ValueError("candidate store is not a directory")
    return path


def _read(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("candidate record must be a regular file")
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("candidate record exceeds size limit")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("candidate record must be an object")
    return obj


def _atomic(path, value):
    data = (canon(value) + "\n").encode()
    if len(data) > MAX_BYTES:
        raise ValueError("candidate index exceeds size limit")
    if path.is_symlink():
        raise ValueError("candidate index must not be a symlink")
    fd, temp = tempfile.mkstemp(prefix=".candidate-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temp).unlink(missing_ok=True)


def read_record(store, record_id):
    if not isinstance(record_id, str) or not ID.fullmatch(record_id):
        raise ValueError("invalid candidate id")
    root = Path(store)
    if root.is_symlink() or (root / "versions").is_symlink():
        raise ValueError("candidate store directories must not be symlinks")
    obj = _read(root / "versions" / f"{record_id}.json")
    if obj.get("schema") != 1 or _digest(obj.get("candidate")) != record_id:
        raise ValueError("candidate checksum mismatch")
    if obj.get("record_sha256") != _digest({"candidate": obj["candidate"], "receipt": obj.get("receipt")}):
        raise ValueError("candidate receipt checksum mismatch")
    return obj


def read_index(store):
    root = Path(store)
    if root.is_symlink():
        raise ValueError("candidate store must not be a symlink")
    try:
        obj = _read(root / "index.json")
    except FileNotFoundError:
        return {"schema": 1, "scopes": {}}
    if obj.get("schema") != 1 or not isinstance(obj.get("scopes"), dict):
        raise ValueError("invalid candidate index")
    for scope_id, scope in obj["scopes"].items():
        if not isinstance(scope, dict) or not isinstance(scope.get("caps"), dict):
            raise ValueError("invalid candidate scope")
        if _digest(scope.get("identity")) != scope_id:
            raise ValueError("candidate scope identity mismatch")
        for cap, record_id in scope["caps"].items():
            record = read_record(root, record_id)["candidate"]
            if record.get("identity") != scope["identity"] or str(record.get("cap")) != cap:
                raise ValueError("candidate pointer crosses identity or cap")
    return obj


def sync(workdir, contract_path, arm, store):
    """Save best completed deepest-stage candidates per cap and update pointers.

    Unchanged leaders create no versions. A later leader replaces only its
    scoped pointer; every prior candidate stays. No weights are fabricated.
    """
    from .contract import load_contract
    c = load_contract(contract_path)
    ledger_path = Path(workdir).resolve() / "ledger.sqlite"
    led = Ledger.open_readonly(ledger_path)
    try:
        led.conn.execute("BEGIN")  # one consistent WAL read snapshot
        ok, bad = led.verify_chain()
        if not ok:
            raise ValueError(f"candidate source chain broken at {bad}")
        latest = led.conn.execute("SELECT epoch FROM trials WHERE gym=? AND arm=? ORDER BY id DESC LIMIT 1", (c.gym, arm)).fetchone()
        epoch = latest[0] if latest else None
        from .series import compute_epoch
        if epoch is not None and epoch != compute_epoch(str(contract_path)):
            raise ValueError("candidate epoch does not match the contract/source/data manifest")
        starts = [e["payload"] for e in led.events("daemon_started")
                  if e["payload"].get("arm") == arm and e["payload"].get("epoch") == epoch]
        hashes = {s.get("arm_def_hash") for s in starts if s.get("arm_def_hash")}
        if len(hashes) > 1:
            raise ValueError("multiple arm definitions in the candidate scope")
        identity = {"gym": c.gym, "arm": arm, "epoch": epoch,
                    "arm_def_hash": next(iter(hashes), None),
                    "contract_sha256": hashlib.sha256(Path(contract_path).read_bytes()).hexdigest(),
                    "run": ledger_path.parent.name,
                    "run_source_id": hashlib.sha256(str(ledger_path).encode()).hexdigest()}
        scope_id = _digest(identity)
        best = {}
        sign = 1 if c.direction == "min" else -1
        for row in led.conn.execute("SELECT id,spec_key,params,metrics,finished_at FROM trials WHERE gym=? AND arm=? AND epoch=? AND stage=? AND state='done' ORDER BY id", (c.gym, arm, epoch, c.stages[-1])):
            metrics = json.loads(row["metrics"] or "{}")
            value, size = metrics.get(c.primary), metrics.get(c.cap_metric) if c.cap_metric else None
            if not _number(value) or not _number(size) or size < 0:
                continue
            spec = c.space.validate(json.loads(row["params"]))
            if c.space.key(spec) != row["spec_key"]:
                raise ValueError("candidate spec key does not match its parameters")
            for cap in c.caps:
                if size <= cap and (str(cap) not in best or sign * value < sign * best[str(cap)]["value"]):
                    best[str(cap)] = {"identity": identity, "trial": row["id"], "spec_key": row["spec_key"],
                        "spec": spec, "stage": c.stages[-1], "metric": c.primary, "direction": c.direction,
                        "value": value, "cap": cap, "n_params": size, "metrics": metrics,
                        "finished_at": row["finished_at"], "status": "development_candidate",
                        "weights_preserved": False, "release_ready": False}
        receipt = {"chain_head": led.head(), "preserved_at": time.time(),
                   "source_hashes": [{"name": Path(p).name, "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest()}
                                     for p in c.code_paths if Path(p).is_file()],
                   "note": "Specification and evidence record only; historical fitted weights were not retained."}
    finally:
        led.close()

    root = _directory(store)
    versions = _directory(root / "versions")
    lock_fd = os.open(root / ".lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        index = read_index(root)
        previous_index = canon(index)
        scope = index["scopes"].setdefault(scope_id, {"identity": identity, "caps": {}})
        saved, retained = [], []
        for cap, candidate in best.items():
            record_id = _digest(candidate)
            incumbent_id = scope["caps"].get(cap)
            if incumbent_id and incumbent_id != record_id:
                incumbent = read_record(root, incumbent_id)["candidate"]
                if sign * candidate["value"] >= sign * incumbent["value"]:
                    retained.append(cap)
                    continue
            path = versions / f"{record_id}.json"
            record = {"schema": 1, "candidate": candidate, "receipt": receipt,
                      "record_sha256": _digest({"candidate": candidate, "receipt": receipt})}
            if path.exists() or path.is_symlink():
                if read_record(root, record_id)["candidate"] != candidate:
                    raise ValueError("candidate record collision")
            else:
                data = (canon(record) + "\n").encode()
                if len(data) > MAX_BYTES:
                    raise ValueError("candidate record exceeds size limit")
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                saved.append(record_id)
            scope["caps"][cap] = record_id
        directory_fd = os.open(versions, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        if canon(index) != previous_index:
            _atomic(root / "index.json", index)
        return {"scope": scope_id, "saved": saved, "retained_incumbent_caps": retained,
                "caps": scope["caps"], "weights_preserved": False}
    finally:
        os.close(lock_fd)


def cmd_candidates(a):
    if a.action == "refit":
        from .model_artifact import refit
        print(json.dumps(refit(a.store,a.id,a.contract,a.dataset,a.horizon,a.out),indent=2))
        return 0
    if a.action == "list":
        print(json.dumps(read_index(a.store), indent=2))
        return 0
    if a.action == "show":
        print(json.dumps(read_record(a.store, a.id), indent=2))
        return 0
    if a.action == "watch":
        import signal
        import threading
        if not math.isfinite(a.interval) or not 1 <= a.interval <= 3600:
            raise ValueError("watch interval must be between 1 and 3600 seconds")
        stop = threading.Event()
        old = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            previous = None
            while not stop.is_set():
                result = sync(a.workdir, a.contract, a.arm, a.store)
                state = (result["caps"], result["retained_incumbent_caps"])
                if previous is None or result["saved"] or state != previous:
                    print(json.dumps(result), flush=True)
                previous = state
                stop.wait(a.interval)
        finally:
            for sig, handler in old.items():
                signal.signal(sig, handler)
        return 0
    print(json.dumps(sync(a.workdir, a.contract, a.arm, a.store), indent=2))
    return 0

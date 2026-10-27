"""Series registry: load, validate and run a pre-registered series of seed runs.

A series file (deploy/series/<name>.toml) declares what to run (arms, seeds, budget, workers),
the expected epoch id (computed from the current evaluator + contract + data) and refuses to start
if the running code would produce a different epoch.  This prevents mixing measurements from two
different evaluators inside one declared series.

The runner delegates to the existing seed runner (seeds.py) after the epoch check passes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

VALID_ARM = re.compile(r"^[a-z0-9_]{1,40}$")
VALID_NAME = re.compile(r"^[a-z0-9_]{1,40}$")
VALID_STATUS = {"planned", "running", "finished"}
REQUIRED = {"name", "question", "arms", "seeds", "max_cpu_s", "workers", "epoch"}
OPTIONAL = {"protocol", "contract", "base", "status"}
ALL_KEYS = REQUIRED | OPTIONAL


class SeriesError(ValueError):
    pass


def load_series(path: str | Path) -> dict:
    """Parse and validate a series TOML file.  Returns the series dict."""
    path = Path(path)
    if not path.exists():
        raise SeriesError(f"series file not found: {path}")
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    unknown = set(raw) - ALL_KEYS
    if unknown:
        raise SeriesError(f"unknown keys in {path.name}: {unknown}")
    missing = REQUIRED - set(raw)
    if missing:
        raise SeriesError(f"missing keys in {path.name}: {missing}")

    name = raw["name"]
    if not VALID_NAME.match(name):
        raise SeriesError(f"bad series name {name!r} (must match {VALID_NAME.pattern})")

    if not isinstance(raw["question"], str) or not raw["question"].strip():
        raise SeriesError("question must be a non-empty string")

    arms = raw["arms"]
    if not isinstance(arms, list) or not arms:
        raise SeriesError("arms must be a non-empty list")
    for arm in arms:
        if not isinstance(arm, str) or not VALID_ARM.match(arm):
            raise SeriesError(f"bad arm name {arm!r} in arms list")

    seeds = raw["seeds"]
    if not isinstance(seeds, str):
        raise SeriesError("seeds must be a string like '1-8' or '1,2,5'")
    from .seeds import parse_seeds
    try:
        parsed_seeds = parse_seeds(seeds)
    except (ValueError, TypeError) as e:
        raise SeriesError(f"bad seeds spec {seeds!r}: {e}") from e

    if not isinstance(raw["max_cpu_s"], (int, float)) or raw["max_cpu_s"] <= 0:
        raise SeriesError("max_cpu_s must be a positive number")

    workers = raw["workers"]
    if not isinstance(workers, int) or workers < 1 or workers > 8:
        raise SeriesError("workers must be an integer 1-8")

    epoch = raw["epoch"]
    if not isinstance(epoch, str) or not re.match(r"^[0-9a-f]{12}$", epoch):
        raise SeriesError(f"epoch must be a 12-hex string, got {epoch!r}")

    status = raw.get("status", "planned")
    if status not in VALID_STATUS:
        raise SeriesError(f"status {status!r} not in {VALID_STATUS}")

    contract = raw.get("contract", "contracts/tinyts_real.toml")
    if not isinstance(contract, str):
        raise SeriesError("contract must be a string path")

    base = raw.get("base", f"/var/lib/ants/series/{name}")
    if not isinstance(base, str) or not Path(base).is_absolute() or ".." in Path(base).parts:
        raise SeriesError(f"base must be an absolute path without '..', got {base!r}")

    return {
        "name": name,
        "question": raw["question"],
        "protocol": raw.get("protocol", ""),
        "contract": contract,
        "arms": arms,
        "seeds": seeds,
        "seeds_parsed": parsed_seeds,
        "max_cpu_s": float(raw["max_cpu_s"]),
        "workers": workers,
        "base": base,
        "epoch": epoch,
        "status": status,
    }


def compute_epoch(contract_path: str | Path) -> str:
    """Compute the epoch id the current code/contract/data would produce.

    Reuses the same logic as Campaign._epoch/_epoch_info without opening a ledger.
    """
    from .contract import load_contract, code_fingerprint

    c = load_contract(contract_path)
    code_fp = code_fingerprint(c.code_paths)[:16]
    contract_hash = hashlib.sha256(
        json.dumps(c.raw, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]

    # data manifest: same path logic as Campaign._data_hash
    data_hash = ""
    if c.code_paths:
        cand = Path(c.code_paths[0]).parents[3] / "data" / "MANIFEST.sha256"
        if cand.exists():
            data_hash = hashlib.sha256(cand.read_bytes()).hexdigest()[:16]

    ident = {"code": code_fp, "contract": contract_hash, "data_manifest": data_hash}
    return hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:12]


def run_series(a) -> int:
    """CLI entry point for `ants series run NAME`."""
    from .cli import _contract_path

    path = Path(a.file) if hasattr(a, "file") and a.file else None
    if path is None:
        # find the series file by name
        for cand in (Path("deploy/series") / f"{a.name}.toml",
                     Path("/opt/ants/deploy/series") / f"{a.name}.toml"):
            if cand.exists():
                path = cand
                break
        if path is None:
            print(f"ants series: no series file found for {a.name!r}", file=sys.stderr)
            return 1

    try:
        s = load_series(path)
    except SeriesError as e:
        print(f"ants series: {e}", file=sys.stderr)
        return 1

    # resolve contract path
    contract = s["contract"]
    try:
        contract = str(Path(_contract_path(contract)).resolve())
    except (SystemExit, FileNotFoundError):
        print(f"ants series: contract not found: {contract}", file=sys.stderr)
        return 1

    # epoch check: the heart of the series safety
    try:
        current = compute_epoch(contract)
    except Exception as e:
        print(f"ants series: cannot compute epoch: {e}", file=sys.stderr)
        return 1

    if current != s["epoch"]:
        print(f"ants series: EPOCH MISMATCH\n"
              f"  series expects: {s['epoch']}\n"
              f"  current code:   {current}\n"
              f"  The evaluator, contract or data manifest has changed.\n"
              f"  Update the epoch in {path} if this is intentional.",
              file=sys.stderr)
        return 5

    # override base from CLI if provided
    base = a.base if hasattr(a, "base") and a.base else s["base"]

    # build a namespace that seeds.run_seeds expects
    ns = type("A", (), {
        "arm": ",".join(s["arms"]),
        "seeds": s["seeds"],
        "base": base,
        "contract": contract,
        "max_evals": None,
        "max_cpu_s": s["max_cpu_s"],
        "workers": s["workers"],
        "dry_run": getattr(a, "dry_run", False),
        "llm_env": getattr(a, "llm_env", None),
        "allow_with_arms": getattr(a, "allow_with_arms", False),
    })()

    if ".." in Path(str(base)).parts:
        print(f"ants series: base must not contain '..': {base}", file=sys.stderr)
        return 1
    # one runner per series directory: two runners would start children on the same workdirs (the children refuse, but only after the mess)
    import fcntl
    Path(base).mkdir(parents=True, exist_ok=True)
    lock = open(Path(base) / ".series.lock", "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"ants series: another `ants series run` already holds {Path(base) / '.series.lock'}", file=sys.stderr)
        return 3
    from .seeds import run_seeds
    try:
        return run_seeds(ns)
    finally:
        lock.close()


def find_series_file(name: str, file: str | None = None) -> Path | None:
    if file:
        return Path(file)
    for cand in (Path("deploy/series") / f"{name}.toml", Path("/opt/ants/deploy/series") / f"{name}.toml"):
        if cand.exists():
            return cand
    return None


def collect_status(s: dict, contract: str, base: Path, values: bool = False) -> tuple[list[dict], str]:
    """One row per (arm, seed) of a series, read-only, and the epoch the current code would give. Shared by `ants series status`, `plan` and the
    terminal interface. The best values per cap are NOT read unless `values` is true: the protocols (E43, section 6) read health and progress between
    looks and nothing that compares arms, and the default of every display follows that."""
    from .seeds import _workdir_name, _best_per_cap, _budget_reached
    from .ledger import Ledger
    try:
        current_epoch = compute_epoch(contract)
    except Exception:
        current_epoch = "?"
    rows = []
    for arm in s["arms"]:
        for seed in s["seeds_parsed"]:
            wd = base / _workdir_name(arm, seed)
            ledger_path = wd / "ledger.sqlite"
            row = {"arm": arm, "seed": seed, "state": "not started", "trials": 0, "cpu_s": 0.0, "budget_s": s["max_cpu_s"],
                   "epoch_ok": None, "def_hash_constant": None, "chain_ok": None}
            if ledger_path.exists():
                led = None
                try:
                    led = Ledger.open_readonly(ledger_path)
                    gym = led.conn.execute("SELECT gym FROM trials LIMIT 1").fetchone()
                    if gym:
                        row["trials"] = led.count_final(gym[0], arm)
                        row["cpu_s"] = round(led.cpu_total(gym[0], arm), 1)
                    epoch_ids = {r["id"] for r in led.conn.execute("SELECT id FROM epochs")}
                    row["epoch_ok"] = s["epoch"] in epoch_ids and len(epoch_ids) == 1
                    starts = led.events("daemon_started")
                    if starts:
                        hashes = {ev["payload"].get("arm_def_hash") for ev in starts if ev["payload"].get("arm") == arm}
                        row["def_hash_constant"] = len(hashes) <= 1
                    row["chain_ok"] = led.verify_chain()[0]
                    running = led.conn.execute("SELECT COUNT(*) FROM trials WHERE state='running'").fetchone()[0]
                    if row["trials"] > 0:
                        row["state"] = "done" if _budget_reached(ledger_path, None, s["max_cpu_s"], arm) else ("running" if running else "partial")
                    elif running > 0:
                        row["state"] = "running"
                except Exception as e:
                    from .textsafe import clean_text
                    row["state"] = f"error: {clean_text(str(e), 60)}"
                finally:
                    if led is not None:
                        led.close()
            if values:
                row["best_per_cap"] = _best_per_cap(wd, arm, contract)
            rows.append(row)
    return rows, current_epoch


def _load_for_cli(a):
    from .cli import _contract_path
    path = find_series_file(a.name, getattr(a, "file", None))
    if path is None:
        print(f"ants series: no series file found for {a.name!r}", file=sys.stderr)
        return None
    try:
        s = load_series(path)
    except SeriesError as e:
        print(f"ants series: {e}", file=sys.stderr)
        return None
    try:
        contract = str(Path(_contract_path(s["contract"])).resolve())
    except (SystemExit, FileNotFoundError):
        print(f"ants series: contract not found: {s['contract']}", file=sys.stderr)
        return None
    return s, contract, Path(a.base if getattr(a, "base", None) else s["base"])


def series_status(a) -> int:
    """CLI entry point for `ants series status NAME [--json] [--values]`."""
    loaded = _load_for_cli(a)
    if loaded is None:
        return 1
    s, contract, base = loaded
    rows, current_epoch = collect_status(s, contract, base, values=getattr(a, "values", False))
    if getattr(a, "json", False):
        print(json.dumps({"series": s["name"], "epoch_expected": s["epoch"], "epoch_current": current_epoch, "rows": rows}, indent=2))
        return 0
    print(f"series: {s['name']}  epoch: {s['epoch']} (current: {current_epoch}{'  OK' if current_epoch == s['epoch'] else '  MISMATCH'})")
    print(f"{'arm':<24} {'seed':>4}  {'state':<14}  {'trials':>6}  {'cpu_s':>8}  {'of':>7}  {'epoch':>6}  {'def':>4}  {'chain':>6}")
    for r in rows:
        eo = "ok" if r["epoch_ok"] else ("FAIL" if r["epoch_ok"] is not None else "-")
        dh = "ok" if r["def_hash_constant"] else ("FAIL" if r["def_hash_constant"] is not None else "-")
        ch = "ok" if r["chain_ok"] else ("FAIL" if r["chain_ok"] is not None else "-")
        print(f"{r['arm']:<24} {r['seed']:>4}  {r['state']:<14}  {r['trials']:>6}  {r['cpu_s']:>8.1f}  {r['budget_s']:>7.0f}  {eo:>6}  {dh:>4}  {ch:>6}")
    if not getattr(a, "values", False):
        print("(values per cap are hidden between the looks of the protocol; --values shows them)")
    return 0


def plan(s: dict, workers: int | None = None, cpu_count: int | None = None, mem_available_mb: float | None = None) -> dict:
    """What a series will ask of the machine, in plain numbers: runs, CPU-hours, wall time, memory. Pure (testable); the CLI fills in the machine."""
    runs = len(s["arms"]) * len(s["seeds_parsed"])
    total_cpu_h = runs * s["max_cpu_s"] / 3600
    w = s["workers"] if workers is None else workers
    if not isinstance(w, int) or isinstance(w, bool) or not 1 <= w <= 8:
        raise SeriesError("workers must be an integer from 1 to 8")
    eff = 0.9                                                    # a worker is not busy 100% (promotion, ledger writes, the supervisor)
    effective_workers = min(w, cpu_count) if cpu_count and cpu_count > 0 else w
    wall_h = total_cpu_h / (effective_workers * eff)
    peak_mb = 270 + 120                                          # one evaluation's largest measured peak (266 MB on the server) + a run's own process
    need_mb = w * peak_mb
    notes = []
    if cpu_count and w > cpu_count:
        notes.append(f"{w} workers on {cpu_count} CPUs: they will share, and every run takes longer in wall time (its CPU budget is unchanged)")
    if mem_available_mb is not None and need_mb > mem_available_mb:
        notes.append(f"about {need_mb} MB estimated for {w} workers, {mem_available_mb:.0f} MB available: memory pressure is possible")
    return {"runs": runs, "cpu_hours_per_run": round(s["max_cpu_s"] / 3600, 2), "total_cpu_hours": round(total_cpu_h, 1), "workers": w,
            "expected_wall_hours": round(wall_h, 1), "expected_wall_days": round(wall_h / 24, 1), "memory_mb_estimate": need_mb,
            "assumptions": {"cpu_efficiency": eff, "memory_mb_per_worker": peak_mb, "cpu_quota_detected": False}, "notes": notes}


def mem_available_mb() -> float | None:
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    except OSError:
        pass
    return None


def series_plan(a) -> int:
    """CLI entry point for `ants series plan NAME [--workers N] [--json]`."""
    loaded = _load_for_cli(a)
    if loaded is None:
        return 1
    s, contract, base = loaded
    try:
        p = plan(s, getattr(a, "workers", None), os.cpu_count(), mem_available_mb())
    except SeriesError as e:
        print(f"ants series: {e}", file=sys.stderr)
        return 1
    if getattr(a, "json", False):
        print(json.dumps(p, indent=2))
        return 0
    print(f"series {s['name']}: {p['runs']} runs of {p['cpu_hours_per_run']} CPU-hours = {p['total_cpu_hours']} CPU-hours")
    print(f"  with {p['workers']} workers: about {p['expected_wall_hours']} hours ({p['expected_wall_days']} days) of wall time, about {p['memory_mb_estimate']} MB of memory")
    print("  estimate assumes 90% CPU efficiency and 390 MB per worker; service CPU quota is not detected")
    for n in p["notes"]:
        print(f"  note: {n}")
    return 0


def series_check(a) -> int:
    """CLI entry point for `ants series check NAME`.  Exit non-zero if any integrity problem."""
    from .cli import _contract_path

    path = Path(a.file) if hasattr(a, "file") and a.file else None
    if path is None:
        for cand in (Path("deploy/series") / f"{a.name}.toml",
                     Path("/opt/ants/deploy/series") / f"{a.name}.toml"):
            if cand.exists():
                path = cand
                break
        if path is None:
            print(f"ants series: no series file found for {a.name!r}", file=sys.stderr)
            return 1

    try:
        s = load_series(path)
    except SeriesError as e:
        print(f"ants series: {e}", file=sys.stderr)
        return 1

    contract = s["contract"]
    try:
        contract = str(Path(_contract_path(contract)).resolve())
    except (SystemExit, FileNotFoundError):
        print(f"ants series: contract not found: {contract}", file=sys.stderr)
        return 1

    base = Path(a.base if hasattr(a, "base") and a.base else s["base"])
    from .seeds import _workdir_name
    from .ledger import Ledger

    problems = []
    for arm in s["arms"]:
        for seed in s["seeds_parsed"]:
            wd = base / _workdir_name(arm, seed)
            ledger_path = wd / "ledger.sqlite"
            if not ledger_path.exists():
                continue
            try:
                led = Ledger.open_readonly(ledger_path)
                # epoch check
                epochs = [r["id"] for r in led.conn.execute("SELECT id FROM epochs")]
                for eid in epochs:
                    if eid != s["epoch"]:
                        problems.append(f"{arm}_s{seed}: ledger epoch {eid} != series epoch {s['epoch']}")
                # def hash constant
                starts = led.events("daemon_started")
                hashes = set()
                for ev in starts:
                    if ev["payload"].get("arm") == arm:
                        h = ev["payload"].get("arm_def_hash")
                        if h:
                            hashes.add(h)
                if len(hashes) > 1:
                    problems.append(f"{arm}_s{seed}: arm_def_hash changed within ledger: {hashes}")
                # chain
                ok, bad = led.verify_chain()
                if not ok:
                    problems.append(f"{arm}_s{seed}: chain broken at seq {bad}")
                led.close()
            except Exception as e:
                problems.append(f"{arm}_s{seed}: error: {e}")

    if problems:
        for p in problems:
            print(f"FAIL: {p}", file=sys.stderr)
        return 1
    print("all checks passed")
    return 0

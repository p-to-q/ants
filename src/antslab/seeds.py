"""Seed runner: run `ants run` for a list of seeds across one or more arms, K at a time.

Workdirs are DIR/<arm>_s<k>.  A seeds.json is written BEFORE any run starts, recording what
was planned, and a second run with different parameters is refused if it would conflict.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

VALID_ARM = re.compile(r"^[a-z0-9_]{1,40}$")
# BLAS thread pinning: a laptop with 12 children each using 14 BLAS threads reaches load 190
_BLAS_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS")


def parse_seeds(spec: str) -> list[int]:
    """'1-8' -> [1..8], '1,2,5' -> [1,2,5]."""
    if "-" in spec and "," not in spec:
        parts = spec.split("-", 1)
        return list(range(int(parts[0]), int(parts[1]) + 1))
    return [int(s) for s in spec.split(",")]


def _contract_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _workdir_name(arm: str, seed: int) -> str:
    return f"{arm}_s{seed}"


def _check_ledger_matches(ledger_path: Path, arm: str, seed: int) -> str:
    """Check a ledger's first daemon_started to see if it belongs to this arm/seed.
    Returns '' if ok or no ledger, or an error string."""
    if not ledger_path.exists():
        return ""
    from .ledger import Ledger
    try:
        led = Ledger.open_readonly(ledger_path)
        starts = led.events("daemon_started")
        led.close()
    except Exception:
        return ""
    if not starts:
        return ""
    first = starts[0]["payload"]
    if first.get("arm") != arm:
        return f"ledger was started with arm {first.get('arm')!r}, not {arm!r}"
    return ""


def _budget_reached(ledger_path: Path, max_evals: int | None, max_cpu_s: float | None, arm: str) -> bool:
    if max_evals is None and max_cpu_s is None:
        return False
    if not ledger_path.exists():
        return False
    from .ledger import Ledger
    try:
        led = Ledger.open_readonly(ledger_path)
        gym = led.conn.execute("SELECT gym FROM trials LIMIT 1").fetchone()
        if gym is None:
            led.close()
            return False
        gym = gym[0]
        if max_evals is not None and led.count_final(gym, arm) >= max_evals:
            led.close()
            return True
        if max_cpu_s is not None and led.cpu_total(gym, arm) >= max_cpu_s:
            led.close()
            return True
        led.close()
    except Exception:
        pass
    return False


def _best_per_cap(wd: Path, arm: str, contract_path: str) -> dict[str, float | None]:
    """Best rel_mse per cap from a ledger, read-only.  Returns {cap_str: value or None}."""
    out: dict[str, float | None] = {}
    try:
        from .contract import load_contract
        from .ledger import Ledger
        c = load_contract(contract_path)
        if not c.caps or not c.primary:
            return out
        ledger_path = wd / "ledger.sqlite"
        if not ledger_path.exists():
            return {str(cap): None for cap in c.caps}
        led = Ledger.open_readonly(ledger_path)
        # for each cap, find the deepest stage with finished trials whose n_params <= cap, then best primary
        for cap in c.caps:
            best = None
            for stage in reversed(c.stages):
                rows = led.conn.execute(
                    "SELECT metrics FROM trials WHERE gym=? AND arm=? AND stage=? AND state='done' AND metrics IS NOT NULL",
                    (c.gym, arm, stage)).fetchall()
                candidates = []
                for r in rows:
                    m = json.loads(r[0])
                    cap_val = m.get(c.cap_metric, 0) if c.cap_metric else 0
                    if cap_val <= cap and c.primary in m:
                        candidates.append(m[c.primary])
                if candidates:
                    best = min(candidates) if c.direction == "min" else max(candidates)
                    break
            out[str(cap)] = best
        led.close()
    except Exception:
        pass
    return out


def _summary_row(arm: str, seed: int, wd: Path, state: str, contract_path: str) -> dict:
    row = {"arm": arm, "seed": seed, "state": state, "trials": 0, "cpu_s": 0.0, "best_per_cap": {}}
    ledger_path = wd / "ledger.sqlite"
    if not ledger_path.exists():
        return row
    from .ledger import Ledger
    try:
        led = Ledger.open_readonly(ledger_path)
        gym = led.conn.execute("SELECT gym FROM trials LIMIT 1").fetchone()
        if gym:
            gym = gym[0]
            row["trials"] = led.count_final(gym, arm)
            row["cpu_s"] = round(led.cpu_total(gym, arm), 1)
        led.close()
    except Exception:
        pass
    row["best_per_cap"] = _best_per_cap(wd, arm, contract_path)
    return row


MODEL_ARMS = ("llm", "llm_dream", "portfolio_llm", "portfolio_dream", "portfolio_agent", "agent")


def active_arm_units() -> list[str]:
    """the ants@<arm> services that are running on this host (empty where there is no systemd): a series on a 2-vCPU host must not start
    on top of live arms (the kernel would kill an arm first, and every run of both would slow down)"""
    import shutil
    if not shutil.which("systemctl"):
        return []
    try:
        r = subprocess.run(["systemctl", "list-units", "ants@*", "--state=active", "--no-legend", "--plain"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]


def run_seeds(a) -> int:
    from .cli import _contract_path, _resolve
    arms = a.arm.split(",")
    seeds = parse_seeds(a.seeds)
    base = Path(a.base).resolve()
    base.mkdir(parents=True, exist_ok=True)

    # validate arm names
    for arm in arms:
        if not VALID_ARM.match(arm):
            print(f"ants seeds: bad arm name {arm!r} (must match {VALID_ARM.pattern})", file=sys.stderr)
            return 1

    # The daily model-call cap belongs to ONE ledger, so N seeds of a model arm spend N times the cap. No series needs that: refuse it unless asked.
    model_arms = [x for x in arms if x in MODEL_ARMS]
    if model_arms and not getattr(a, "allow_model_arms", False):
        print(f"ants seeds: {', '.join(model_arms)} call a model, and the daily cap is per ledger (each seed would get the whole cap); "
              f"pass --allow-model-arms if that is meant", file=sys.stderr)
        return 1

    # resolve contract
    contract = a.contract
    if contract:
        contract = str(Path(_contract_path(contract)).resolve())
    else:
        ns = type("A", (), {"contract": None, "workdir": ".", "arm": arms[0], "json": True})()
        try:
            _resolve(ns)
            contract = str(Path(ns.contract).resolve())
        except SystemExit:
            print("ants seeds: no contract found; pass --contract", file=sys.stderr)
            return 1

    from .update import current_version
    contract_hash = _contract_sha256(contract)
    meta = {
        "arms": arms, "seeds": seeds, "contract": contract, "contract_sha256": contract_hash,
        "max_evals": a.max_evals, "max_cpu_s": a.max_cpu_s,
        "version": current_version(), "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # check or write seeds.json (pre-registration: arms, seeds, contract hash, budgets must all match)
    seeds_file = base / "seeds.json"
    if seeds_file.exists():
        existing = json.loads(seeds_file.read_text())
        diffs = []
        if set(existing.get("arms", [])) != set(arms):
            diffs.append(f"arms: {existing.get('arms')} vs {arms}")
        if sorted(existing.get("seeds", [])) != sorted(seeds):
            diffs.append(f"seeds: {existing.get('seeds')} vs {seeds}")
        if existing.get("contract_sha256") != contract_hash:
            diffs.append(f"contract_sha256: {existing.get('contract_sha256', '')[:12]} vs {contract_hash[:12]}")
        if existing.get("max_evals") != a.max_evals:
            diffs.append(f"max_evals: {existing.get('max_evals')} vs {a.max_evals}")
        if existing.get("max_cpu_s") != a.max_cpu_s:
            diffs.append(f"max_cpu_s: {existing.get('max_cpu_s')} vs {a.max_cpu_s}")
        if diffs:
            print(f"ants seeds: {seeds_file} already exists with different parameters.\n"
                  f"  differences: {'; '.join(diffs)}\n"
                  f"The seed list is pre-registered; start fresh or use the same parameters.", file=sys.stderr)
            return 1
    else:
        seeds_file.write_text(json.dumps(meta, indent=2) + "\n")
        print(f"wrote {seeds_file}")

    if a.dry_run:
        print("dry run: would run:")
        for seed in seeds:
            for arm in arms:
                wd = base / _workdir_name(arm, seed)
                print(f"  {arm} seed={seed} -> {wd}")
        return 0

    live = [] if getattr(a, "allow_with_arms", False) else active_arm_units()
    if live:
        print(f"ants seeds: {', '.join(live)} are running on this host; a series would compete with them for 2 CPUs and memory "
              f"(stop them, or pass --allow-with-arms)", file=sys.stderr)
        return 6

    # build interleaved work list: s1 of every arm before s2 of any
    work: list[tuple[str, int]] = []
    for seed in seeds:
        for arm in arms:
            work.append((arm, seed))

    # check each workdir before starting
    for arm, seed in work:
        wd = base / _workdir_name(arm, seed)
        ledger_path = wd / "ledger.sqlite"
        err = _check_ledger_matches(ledger_path, arm, seed)
        if err:
            print(f"ants seeds: {wd} has a conflicting ledger: {err}", file=sys.stderr)
            return 1

    # run the work, K workers at a time
    workers = max(1, a.workers)
    active: dict[tuple[str, int], subprocess.Popen] = {}
    log_fds: dict[tuple[str, int], object] = {}           # open log file handles
    results: dict[tuple[str, int], str] = {}
    pending = list(work)
    stop = False

    def _sighandler(sig, frame):
        nonlocal stop
        stop = True
    old_term = signal.signal(signal.SIGTERM, _sighandler)
    old_int = signal.signal(signal.SIGINT, _sighandler)

    def _child_env() -> dict:
        env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
        for v in _BLAS_VARS:
            if v not in env:
                env[v] = "1"
        return env

    def _start_one(arm: str, seed: int) -> subprocess.Popen | None:
        wd = base / _workdir_name(arm, seed)
        ledger_path = wd / "ledger.sqlite"
        if _budget_reached(ledger_path, a.max_evals, a.max_cpu_s, arm):
            return None
        wd.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "antslab.cli", "run",
               "--contract", contract, "--workdir", str(wd), "--arm", arm, "--seed", str(seed)]
        if a.max_evals is not None:
            cmd += ["--max-evals", str(a.max_evals)]
        if a.max_cpu_s is not None:
            cmd += ["--max-cpu-s", str(a.max_cpu_s)]
        if a.llm_env:
            cmd += ["--llm-env", a.llm_env]
        log = open(wd / "run.log", "a")
        log_fds[(arm, seed)] = log
        return subprocess.Popen(cmd, env=_child_env(), stdout=log, stderr=log)

    try:
        while (pending or active) and not stop:
            while pending and len(active) < workers and not stop:
                arm, seed = pending[0]
                wd = base / _workdir_name(arm, seed)
                if _budget_reached(wd / "ledger.sqlite", a.max_evals, a.max_cpu_s, arm):
                    pending.pop(0)
                    results[(arm, seed)] = "done (budget reached before start)"
                    continue
                p = _start_one(arm, seed)
                pending.pop(0)
                if p is None:
                    results[(arm, seed)] = "done (budget reached before start)"
                else:
                    active[(arm, seed)] = p
                    print(f"started {arm} seed={seed} pid={p.pid} -> {wd}")

            finished = []
            for key, proc in active.items():
                rc = proc.poll()
                if rc is not None:
                    finished.append((key, rc))
            for key, rc in finished:
                arm, seed = key
                del active[key]
                lf = log_fds.pop(key, None)
                if lf:
                    lf.close()
                if rc == 0:
                    results[key] = "done"
                    print(f"finished {arm} seed={seed} (exit 0)")
                else:
                    results[key] = f"exit {rc}"
                    # print last line of run.log for diagnosis
                    logpath = base / _workdir_name(arm, seed) / "run.log"
                    last_line = ""
                    try:
                        lines = logpath.read_text().strip().splitlines()
                        last_line = lines[-1] if lines else ""
                    except Exception:
                        pass
                    print(f"finished {arm} seed={seed} (exit {rc}): {last_line}")

            if active and not finished:
                time.sleep(1)

    finally:
        if stop and active:
            print("stopping children...")
            for key, proc in active.items():
                try:
                    proc.terminate()
                except OSError:
                    pass
            deadline = time.monotonic() + 30
            for key, proc in active.items():
                remaining = max(0, deadline - time.monotonic())
                try:
                    proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    proc.kill()
                results[key] = "interrupted"
        for lf in log_fds.values():
            try:
                lf.close()
            except Exception:
                pass
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)

    # load contract caps for the summary header
    try:
        from .contract import load_contract
        c = load_contract(contract)
        caps = sorted(c.caps) if c.caps else []
    except Exception:
        caps = []

    # print summary table
    cap_hdrs = [f"best<={cap}" for cap in caps]
    print("\n--- summary ---")
    hdr = f"{'arm':<24} {'seed':>4}  {'state':<30}  {'trials':>6}  {'cpu_s':>8}"
    if cap_hdrs:
        hdr += "  " + "  ".join(f"{h:>12}" for h in cap_hdrs)
    print(hdr)
    for arm, seed in work:
        wd = base / _workdir_name(arm, seed)
        state = results.get((arm, seed), "not started")
        row = _summary_row(arm, seed, wd, state, contract)
        line = f"{arm:<24} {seed:>4}  {state:<30}  {row['trials']:>6}  {row['cpu_s']:>8.1f}"
        if caps:
            for cap in caps:
                val = row["best_per_cap"].get(str(cap))
                line += f"  {val:>12.4f}" if val is not None else f"  {'-':>12}"
            pass
        print(line)

    if stop:
        return 1
    codes = {int(str(v).split()[1]) for v in results.values() if str(v).startswith("exit ") and str(v).split()[1].lstrip("-").isdigit()}
    for code in (4, 3):                       # a child that refused to start (4 definition changed, 3 workdir already in use) will refuse again:
        if code in codes:                     # tell the unit not to restart us (RestartPreventExitStatus)
            return code
    if any("exit" in str(v) and v != "done" for v in results.values()):
        return 1
    return 0

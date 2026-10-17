"""Seed runner: run `ants run` for a list of seeds across one or more arms, K at a time.

Workdirs are DIR/<arm>_s<k>.  A seeds.json is written BEFORE any run starts, recording what
was planned, and a second run with different parameters is refused if it would conflict.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


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
    """Check if a ledger has reached its budget."""
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
        if max_evals is not None:
            n = led.count_final(gym, arm)
            if n >= max_evals:
                led.close()
                return True
        if max_cpu_s is not None:
            cpu = led.cpu_total(gym, arm)
            if cpu >= max_cpu_s:
                led.close()
                return True
        led.close()
    except Exception:
        pass
    return False


def _summary_row(arm: str, seed: int, wd: Path, state: str) -> dict:
    """Build a summary row for one seed."""
    row = {"arm": arm, "seed": seed, "state": state, "trials": 0, "cpu_s": 0.0, "best_rel_mse": {}}
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
    return row


def run_seeds(a) -> int:
    from .cli import _contract_path, _resolve
    arms = a.arm.split(",")
    seeds = parse_seeds(a.seeds)
    base = Path(a.base).resolve()
    base.mkdir(parents=True, exist_ok=True)

    # resolve contract
    contract = a.contract
    if contract:
        contract = str(Path(_contract_path(contract)).resolve())
    else:
        # use _resolve logic to find one
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

    # check or write seeds.json
    seeds_file = base / "seeds.json"
    if seeds_file.exists():
        existing = json.loads(seeds_file.read_text())
        # check compatibility: same arms and seeds
        if set(existing["arms"]) != set(arms) or sorted(existing["seeds"]) != sorted(seeds):
            print(f"ants seeds: {seeds_file} already exists with different arms/seeds.\n"
                  f"  existing: arms={existing['arms']} seeds={existing['seeds']}\n"
                  f"  requested: arms={arms} seeds={seeds}\n"
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
    results: dict[tuple[str, int], str] = {}
    pending = list(work)
    stop = False

    def _sighandler(sig, frame):
        nonlocal stop
        stop = True
    old_term = signal.signal(signal.SIGTERM, _sighandler)
    old_int = signal.signal(signal.SIGINT, _sighandler)

    def _start_one(arm: str, seed: int) -> subprocess.Popen | None:
        wd = base / _workdir_name(arm, seed)
        ledger_path = wd / "ledger.sqlite"
        if _budget_reached(ledger_path, a.max_evals, a.max_cpu_s, arm):
            return None  # already done
        cmd = [sys.executable, "-m", "antslab.cli", "run",
               "--contract", contract, "--workdir", str(wd), "--arm", arm, "--seed", str(seed)]
        if a.max_evals is not None:
            cmd += ["--max-evals", str(a.max_evals)]
        if a.max_cpu_s is not None:
            cmd += ["--max-cpu-s", str(a.max_cpu_s)]
        if a.llm_env:
            cmd += ["--llm-env", a.llm_env]
        env = {**os.environ, "ANTS_ALLOWED_ROLES": "dev"}
        return subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        while (pending or active) and not stop:
            # start new workers up to the limit
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

            # poll active processes
            finished = []
            for key, proc in active.items():
                rc = proc.poll()
                if rc is not None:
                    finished.append((key, rc))
            for key, rc in finished:
                arm, seed = key
                del active[key]
                if rc == 0:
                    results[key] = "done"
                    print(f"finished {arm} seed={seed} (exit 0)")
                else:
                    results[key] = f"exit {rc}"
                    stderr = active.get(key)
                    print(f"finished {arm} seed={seed} (exit {rc})")

            if active and not finished:
                time.sleep(1)  # avoid busy-wait

    finally:
        # on stop, terminate children cleanly
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
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)

    # print summary table
    print("\n--- summary ---")
    print(f"{'arm':<24} {'seed':>4}  {'state':<30}  {'trials':>6}  {'cpu_s':>8}")
    for arm, seed in work:
        wd = base / _workdir_name(arm, seed)
        state = results.get((arm, seed), "not started")
        row = _summary_row(arm, seed, wd, state)
        print(f"{arm:<24} {seed:>4}  {state:<30}  {row['trials']:>6}  {row['cpu_s']:>8.1f}")

    if stop:
        return 1
    if any("exit" in str(v) and v != "done" for v in results.values()):
        return 1
    return 0

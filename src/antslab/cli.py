"""antslab command line. Everything stateful lives in <workdir>/ledger.sqlite."""
from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import sys
import threading
from pathlib import Path

from .archive import LocalDirArchive, archive_pending
from .campaign import AlreadyRunning, Campaign, InstanceLock, Shutdown
from .contract import load_contract
from .dream import dream, latest_memory
from .janitor import disk_level, execute_cleanup, free_gb, plan_cleanup
from .ledger import Ledger
from .proposers import make_proposer


CONTRACT_SEARCH = ("/opt/ants/contracts/tinyts.toml", "contracts/tinyts_real.toml", "contracts/tinyts_synth_hard.toml")


def _root() -> Path | None:
    """The directory that carries contracts/ and data/ when ants runs from a package rather than a checkout (the npm launcher sets
    ANTS_ROOT to the package directory)."""
    import os
    r = os.environ.get("ANTS_ROOT")
    return Path(r) if r and Path(r).is_dir() else None


def _contract_path(arg: str) -> str:
    """A path as given; or, when ANTS_ROOT is set, a bundled contract by name (`tinyts_synth_hard`, `tinyts_synth_hard.toml`)."""
    if Path(arg).exists():
        return arg
    root = _root()
    if root:
        for cand in (root / "contracts" / arg, root / "contracts" / f"{arg}.toml", root / arg):
            if cand.is_file():
                return str(cand)
    return arg


def _default_llm_env() -> str:
    import os
    for cand in ("~/.config/ants/llm.env", "/etc/ants/llm.env"):
        if os.path.exists(os.path.expanduser(cand)):
            return cand
    return "~/.config/ants/llm.env"


def _resolve(a):
    """Fill in contract / workdir / arm so that plain `ants shell` works. Order: explicit flag > environment
    (ANTS_CONTRACT, ANTS_WORKDIR, ANTS_ARM) > the installed service > the most recently used ./runs/* ledger > defaults.
    What was chosen is printed to stderr (not in --json mode) so nothing is guessed silently."""
    import os, sys
    chosen = []
    if not getattr(a, "contract", None):
        bundled = [str(_root() / "contracts" / "tinyts_synth_hard.toml")] if _root() else []
        a.contract = os.environ.get("ANTS_CONTRACT") or next((c for c in (*CONTRACT_SEARCH, *bundled) if Path(c).exists()), None)
        if not a.contract:
            raise SystemExit("no contract found: pass --contract, or set ANTS_CONTRACT (looked in: " + ", ".join(CONTRACT_SEARCH) + ")")
        chosen.append(f"contract={a.contract}")
    a.contract = _contract_path(a.contract)
    if not getattr(a, "workdir", None):
        a.workdir = os.environ.get("ANTS_WORKDIR")
        if not a.workdir:
            svc = sorted(Path("/var/lib/ants").glob("*/ledger.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True) if Path("/var/lib/ants").exists() else []
            runs = sorted(Path("runs").glob("*/ledger.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True) if Path("runs").exists() else []
            a.workdir = str((svc or runs)[0].parent) if (svc or runs) else "runs/default"
        chosen.append(f"workdir={a.workdir}")
    if getattr(a, "arm", None) in (None, ""):
        a.arm = os.environ.get("ANTS_ARM") or ""
        db = Path(a.workdir) / "ledger.sqlite"
        if not a.arm and db.exists():
            import sqlite3
            try:
                r = sqlite3.connect(db).execute("SELECT arm FROM trials ORDER BY id DESC LIMIT 1").fetchone()
                a.arm = r[0] if r else ""
            except sqlite3.Error:
                a.arm = ""
        a.arm = a.arm or "portfolio"
        chosen.append(f"arm={a.arm}")
    if chosen and not getattr(a, "json", False):
        print("using " + " ".join(chosen), file=sys.stderr)
    return a


def _open(a):
    _resolve(a)
    a.workdir, a.contract = str(Path(a.workdir).resolve()), str(Path(a.contract).resolve())    # gyms run with another cwd
    wd = Path(a.workdir); wd.mkdir(parents=True, exist_ok=True)
    return wd, Ledger(wd / "ledger.sqlite"), load_contract(a.contract)


def cmd_run(a):
    _resolve(a)
    a.workdir = str(Path(a.workdir).resolve())
    wd = Path(a.workdir); wd.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(wd)
    try:
        lock.acquire()                    # before touching the ledger: a second daemon would requeue the first one's live trial
    except AlreadyRunning as e:
        print(f"ants: {e}", file=sys.stderr)
        return 3
    wd, led, c = _open(a)
    client = None
    if a.arm in ("llm", "llm_dream", "portfolio_llm", "portfolio_dream"):
        from .llm import LLMClient, load_endpoints
        client = LLMClient(load_endpoints(a.llm_env or _default_llm_env()), led)
    mem = (lambda arm: latest_memory(led, arm)) if a.arm in ("llm_dream", "portfolio_dream") else None
    p = make_proposer(a.arm, c, a.seed, client, mem)
    camp = Campaign(led, c, wd, a.arm, p, a.seed, a.max_evals, a.max_cpu_s,
                    dream_every=a.dream_every if a.arm in ("llm_dream", "portfolio_dream") else 0,
                    dream_fn=(lambda: dream(led, c, a.arm, client)) if a.arm in ("llm_dream", "portfolio_dream") else None)
    stop = {"v": False}

    def on_signal(*_):
        stop["v"] = True                  # a running trial notices within ~0.25 s (run_command polls the flag) ...
        if camp.interruptible:            # ... and a blocking LLM call, with nothing in flight, is abandoned on the spot
            raise Shutdown()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, on_signal)
    print(camp.run(stop=lambda: stop["v"]), flush=True)
    if threading.active_count() > 1:
        # LLM worker threads (ThreadPoolExecutor, non-daemon) may still sit in a socket read for up to their 100 s timeout, and
        # the interpreter would join them at exit, after a stop signal AND after a normal finish (budget reached): systemd would
        # SIGKILL us after 60 s. Everything we own is already committed; the instance lock is released by the OS.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)
    return 0


def cmd_status(a):
    wd, led, c = _open(a)
    ok, bad = led.verify_chain()
    print(json.dumps({"counts": led.counts(c.gym, a.arm), "free_gb": round(free_gb(wd), 1),
                      "disk_level": disk_level(free_gb(wd), c.thresholds),
                      "chain_ok": ok, "chain_bad_seq": bad, "head": led.head()[:16],
                      "unseals": led.unseal_count(c.gym)}, indent=1))
    return 0 if ok else 2


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("ants-lab")
    except Exception:
        return "unknown"


def _cell(v):
    """CSV cell safe for spreadsheets: text that would be read as a formula is neutralised (strings only; numbers stay numbers)."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def cmd_export(a):
    wd, led, c = _open(a)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    cols = ["id", "arm", "stage", "state", "source", "spec_key", "seed", "attempt", "cpu_s", "error"]
    with open(out / "trials.csv", "w", newline="") as f:
        w = csv.writer(f)
        keys = list(c.space.params)
        w.writerow(cols + [f"p_{k}" for k in keys] + ["m_" + c.primary] + ([f"m_{c.cap_metric}"] if c.cap_metric else []))
        for t in led.trials(c.gym, a.arm):
            m = t["metrics"] or {}
            w.writerow([_cell(x) for x in [t[k] for k in cols] + [t["params"].get(k) for k in keys] + [m.get(c.primary)]
                        + ([m.get(c.cap_metric)] if c.cap_metric else [])])
    with open(out / "events.jsonl", "w") as f:
        for e in led.events():
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"wrote {out}/trials.csv and events.jsonl (chain head {led.head()[:16]})")
    return 0


def cmd_board(a):
    from .board import build
    wd, led, c = _open(a)
    arms = a.arms.split(",") if a.arms else sorted({t["arm"] for t in led.trials(c.gym)})
    build(led, c, arms, a.out)
    print(f"wrote {a.out}/board.html and board.json")
    return 0


def cmd_serve(a):
    """Serve the board on localhost only (reach it through an SSH tunnel) and regenerate it periodically."""
    import threading, time
    from .board import build, make_server
    wd, led, c = _open(a)
    arms = a.arms.split(",") if a.arms else None
    def loop():
        while True:
            try:
                build(Ledger(wd / "ledger.sqlite"), c, arms or sorted({t["arm"] for t in led.trials(c.gym)}), a.out)
            except Exception as e:                      # the board is optional: never let it die loudly
                print("board refresh failed:", e)
            time.sleep(a.every)
    threading.Thread(target=loop, daemon=True).start()
    srv = make_server(a.out, a.port)                    # loopback only, text-safe handler (see board.py)
    print(f"http://127.0.0.1:{a.port}/board.html"); srv.serve_forever()
    return 0


def cmd_data(a):
    from .data import Catalog
    cat = Catalog(a.catalog)
    if a.action == "list":
        import collections
        rows = collections.defaultdict(list)
        for n in cat.names():
            m = cat.meta[n]
            if not n.startswith("syn_") or a.all:
                rows[m["role"]].append(f"{n}({m['family']})")
        for role in ("dev", "confirm", "sealed", "stress"):
            print(f"{role:8s} {len(rows[role]):3d}  " + ", ".join(rows[role][:40]) + (" ..." if len(rows[role]) > 40 else ""))
        print(f"synthetic entries: {sum(1 for n in cat.names() if n.startswith('syn_'))}")
        return 0
    if a.action == "verify":
        bad = cat.verify_manifest(only_existing=a.partial)
        print("raw files intact" if not bad else "PROBLEMS:\n  " + "\n  ".join(bad))
        return 0 if not bad else 2
    if a.action == "build":
        names = a.names.split(",") if a.names else None
        rep = cat.build_processed(names)
        print(f"built {len(rep)} datasets -> {cat.base}/processed")
        return 0
    return 1


def _emit(a, obj, text_fn):
    if getattr(a, "json", False):
        print(json.dumps(obj, indent=1, default=str))
    else:
        print(text_fn(obj))


def cmd_top(a):
    import time
    from . import ui
    wd, led, c = _open(a)
    from .history import History
    h = History(led, c, a.arm)
    try:
        while True:
            snap = ui.snapshot(led, c, a.arm, wd, h)
            if a.json:
                print(json.dumps(snap, default=str)); return 0
            print(("" if a.once else "\033[2J\033[H") + ui.render_top(snap))
            if a.once:
                return 0
            time.sleep(a.every)
    except KeyboardInterrupt:
        return 0


def cmd_best(a):
    from . import ui
    from .history import History
    wd, led, c = _open(a)
    h = History(led, c, a.arm); h.update()
    lb = ui.leaderboard(h, c, a.top)
    if a.cap:
        lb = {k: v for k, v in lb.items() if k == str(a.cap)}
    _emit(a, lb, lambda d: "\n".join(f"cap <= {k}\n" + "\n".join(f"  {r['rank']}. {r['value']:.4f}  {r['n_params']} params  trial {r['trial']}  {r['operator']}  {r['spec'][:70]}" for r in v) for k, v in d.items()))
    return 0


def cmd_show(a):
    from . import ui
    wd, led, c = _open(a)
    d = ui.show(led, c, a.ref)
    _emit(a, d, lambda x: ui.render_show(x))
    return 1 if "not_found" in d else 0


def cmd_log(a):
    from . import ui
    wd, led, c = _open(a)
    rows = ui.log(led, a.n, a.kind)
    _emit(a, rows, lambda d: "\n".join(f"{r['seq']:>7} {r['time']} {r['kind']:<18} " + " ".join(f"{k}={v}" for k, v in r.items() if k not in ("seq", "time", "kind")) for r in d))
    return 0


def cmd_hint(a):
    from . import ui
    wd, led, c = _open(a)
    n = ui.hint(led, a.text)
    print(f"recorded (hint #{n}); the next LLM prompts will include it. Hints never change gates or budgets.")
    return 0


def cmd_pause(a):
    from . import ui
    wd, led, c = _open(a)
    f = Path(wd) / ui.PAUSE_FILE
    if a.resume:
        f.unlink(missing_ok=True); print("resumed")
    else:
        f.write_text("paused by user"); print("paused: the loop finishes the current step and idles")
    return 0


def cmd_doctor(a):
    from . import ui
    wd, led, c = _open(a)
    res = ui.doctor(led, c, a.arm, wd)
    _emit(a, [{"level": l, "message": m} for l, m in res], lambda d: "\n".join(f"[{x['level']:>4}] {x['message']}" for x in d))
    return 2 if any(l == "FAIL" for l, _ in res) else 0


SHELL_COMMANDS = ("top", "best", "show", "log", "hint", "pause", "resume", "doctor", "status", "export", "board")


def cmd_shell(a):
    """Interactive harness: one session, many questions. Every line is a normal subcommand (`best --top 3`, `show 12`,
    `hint try longer look-backs`, `pause`), with the contract/workdir/arm already filled in. `help`, `quit`."""
    import cmd, shlex
    _resolve(a)
    base = ["--contract", a.contract, "--workdir", a.workdir, "--arm", a.arm]

    class Shell(cmd.Cmd):
        intro = f"ants shell · arm {a.arm} · workdir {a.workdir}\ncommands: {', '.join(SHELL_COMMANDS)} · help · quit"
        prompt = "antslab> "

        def default(self, line):
            parts = shlex.split(line)
            if parts[0] in ("quit", "exit", "q"):
                return True
            if parts[0] not in SHELL_COMMANDS:
                print(f"unknown command {parts[0]!r}; try: {', '.join(SHELL_COMMANDS)}"); return
            rest = parts[1:]
            if parts[0] == "hint":                      # free text, no quoting needed
                rest = [" ".join(rest)]
            if parts[0] == "export" and "--out" not in rest:
                rest += ["--out", str(Path(a.workdir) / "export")]
            if parts[0] == "board" and "--out" not in rest:
                rest += ["--out", str(Path(a.workdir) / "board")]
            try:
                main([parts[0], *base[:4], *(["--arm", a.arm] if parts[0] not in ("hint", "pause", "resume") else []), *rest])
            except SystemExit:
                pass
            except Exception as e:                      # a typo must never kill the session
                print(f"error: {type(e).__name__}: {e}")

        def completenames(self, text, *ignored):
            return [c for c in SHELL_COMMANDS + ("help", "quit") if c.startswith(text)]

        def do_help(self, arg):
            print("top [--every 5]        live dashboard (Ctrl-C returns)\nbest [--top 5] [--cap N]  leaderboard per parameter cap\n"
                  "show <trial|key>       spec, stage metrics and lineage of one model\nlog [-n 20] [--kind K]  recent ledger events\n"
                  "hint <text>            tell the proposers something (a suggestion, never a rule)\npause / resume        stop / continue new work\n"
                  "doctor                 health check (non-zero exit on FAIL)\nexport / board        write CSV+JSONL / HTML board\nAdd --json to most commands for machine-readable output.")

        def do_quit(self, arg):
            return True
        do_exit = do_EOF = do_quit

        def emptyline(self):
            pass

    try:
        Shell().cmdloop()
    except KeyboardInterrupt:
        print()
    return 0


def cmd_janitor(a):
    wd, led, c = _open(a)
    if a.archive_dir:
        print("archive:", archive_pending(led, LocalDirArchive(a.archive_dir)))
    lvl = disk_level(free_gb(wd), c.thresholds)
    need = int(max(0.0, (c.thresholds["warn"] - free_gb(wd))) * (1 << 30)) if lvl != "ok" else 0
    plan = plan_cleanup(led, need or a.force_bytes, keep_trials=[]) if (need or a.force_bytes) else []
    print(f"level={lvl}", execute_cleanup(led, plan, dry_run=a.dry_run))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ants")
    ap.add_argument("--version", action="version", version="ants " + _version())
    sub = ap.add_subparsers(dest="cmd", required=False)
    for name, fn in (("run", cmd_run), ("status", cmd_status), ("export", cmd_export), ("janitor", cmd_janitor), ("board", cmd_board), ("serve", cmd_serve),
                     ("top", cmd_top), ("best", cmd_best), ("show", cmd_show), ("log", cmd_log), ("hint", cmd_hint),
                     ("pause", cmd_pause), ("resume", cmd_pause), ("doctor", cmd_doctor), ("shell", cmd_shell)):
        s = sub.add_parser(name)
        s.add_argument("--contract"); s.add_argument("--workdir")
        s.add_argument("--arm", default=None); s.add_argument("--seed", type=int, default=0)
        s.set_defaults(fn=fn)
        if name == "run":
            s.add_argument("--max-evals", type=int); s.add_argument("--max-cpu-s", type=float)
            s.add_argument("--llm-env", default=None, help="env file with LLM_<TAG>_BASE/KEY/MODEL (default ~/.config/ants/llm.env)")
            s.add_argument("--dream-every", type=int, default=100)
        if name == "export":
            s.add_argument("--out", required=True)
        s.add_argument("--json", action="store_true", help="machine-readable output for other agents")
        if name == "top":
            s.add_argument("--every", type=float, default=5); s.add_argument("--once", action="store_true")
        if name == "best":
            s.add_argument("--top", type=int, default=5); s.add_argument("--cap", type=int)
        if name == "show":
            s.add_argument("ref", help="trial id or spec-key prefix")
        if name == "log":
            s.add_argument("-n", type=int, default=20); s.add_argument("--kind")
        if name == "hint":
            s.add_argument("text")
        if name in ("pause", "resume"):
            s.set_defaults(resume=(name == "resume"))
        if name in ("board", "serve"):
            s.add_argument("--out", required=True); s.add_argument("--arms")
        if name == "serve":
            s.add_argument("--port", type=int, default=8765); s.add_argument("--every", type=int, default=60)
        if name == "janitor":
            s.add_argument("--archive-dir"); s.add_argument("--dry-run", action="store_true")
            s.add_argument("--force-bytes", type=int, default=0)
    d = sub.add_parser("data"); d.add_argument("action", choices=["list", "verify", "build"])
    d.add_argument("--catalog", default="data/catalog.toml"); d.add_argument("--names"); d.add_argument("--all", action="store_true"); d.add_argument("--partial", action="store_true", help="verify only the files that exist (dev-tier installs)")
    d.set_defaults(fn=cmd_data)
    a = ap.parse_args(argv)
    if a.cmd is None:                                 # plain `ants`: open the interactive shell
        a = ap.parse_args(["shell"])
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

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
    from .home import package_root
    root = _root() or package_root()           # a bundled contract can be named in a source checkout too; it is never picked silently as a default
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


def _config_run() -> dict:
    from .home import load_config
    return load_config().get("run", {})


def _resolve_workdir(a, chosen=None):
    """flag > ANTS_WORKDIR > ~/.ants/config.toml > an installed service's ledger > the newest ./runs/* ledger > runs/default"""
    import os
    if getattr(a, "workdir", None):
        return a
    a.workdir = os.environ.get("ANTS_WORKDIR") or _config_run().get("workdir")
    if not a.workdir:
        from .home import ledgers_in
        svc, runs = ledgers_in("/var/lib/ants"), ledgers_in("runs")
        a.workdir = str((svc or runs)[0].parent) if (svc or runs) else "runs/default"
    if chosen is not None:
        chosen.append(f"workdir={a.workdir}")
    return a


def _resolve(a):
    """Fill in contract / workdir / arm so that plain `ants shell` works. Order: explicit flag > environment
    (ANTS_CONTRACT, ANTS_WORKDIR, ANTS_ARM) > ~/.ants/config.toml (written by `ants onboard`) > the installed service > the most recently
    used ./runs/* ledger > defaults. What was chosen is printed to stderr (not in --json mode) so nothing is guessed silently."""
    import os, sys
    chosen = []
    cfg = _config_run()
    if not getattr(a, "contract", None):
        bundled = [str(_root() / "contracts" / "tinyts_synth_hard.toml")] if _root() else []
        a.contract = os.environ.get("ANTS_CONTRACT") or cfg.get("contract") or next((c for c in (*CONTRACT_SEARCH, *bundled) if Path(c).exists()), None)
        if not a.contract:
            raise SystemExit("no contract found: run `ants onboard`, pass --contract, or set ANTS_CONTRACT (looked in: " + ", ".join(CONTRACT_SEARCH) + ")")
        chosen.append(f"contract={a.contract}")
    a.contract = _contract_path(a.contract)
    _resolve_workdir(a, chosen) if not getattr(a, "workdir", None) else None
    if getattr(a, "arm", None) in (None, ""):
        a.arm = os.environ.get("ANTS_ARM") or cfg.get("arm") or ""
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


def _definition_guard(led, arm: str, cur: dict, new_definition: bool) -> int | None:
    """Check the ledger's last daemon_started arm_def_hash FOR THIS ARM against the current code.
    Returns exit code 4 on mismatch (unless new_definition is set), None to proceed normally."""
    import hashlib as _h, json as _j
    # filter daemon_started events to those belonging to this arm
    all_starts = led.recent_events("daemon_started", 50)
    starts = [e for e in all_starts if e["payload"].get("arm") == arm]
    if not starts:
        return None  # fresh ledger or no prior start for this arm
    last = starts[-1]["payload"]
    prev_hash = last.get("arm_def_hash")
    if not prev_hash:
        return None  # very old daemon_started without a hash
    cur_hash = _h.sha256(_j.dumps(cur, sort_keys=True).encode()).hexdigest()[:12]
    # the version field is excluded from the comparison: a patch release must not block a restart
    prev_def = last.get("arm_def", {})
    if prev_hash == cur_hash:
        return None  # same definition, proceed
    if new_definition:
        print(f"ants: arm definition changed for arm {arm!r} (previous {prev_hash}, current {cur_hash}); "
              f"proceeding with --new-definition", file=sys.stderr)
        return None  # caller will record definition_changed in the daemon_started event below
    # find differing keys
    diff_keys = sorted(k for k in set(list(prev_def) + list(cur)) if prev_def.get(k) != cur.get(k))
    print(f"ants: the arm definition for {arm!r} has changed since the last start in {led.path}.\n"
          f"  previous hash: {prev_hash}\n  current hash:  {cur_hash}\n"
          f"  differing keys: {', '.join(diff_keys) or '(hash method changed)'}\n"
          f"  Pass --new-definition to start anyway (the change is recorded).\n"
          f"  See docs/upgrading.md for when to use a fresh workdir instead.", file=sys.stderr)
    return 4


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
    if a.arm in ("llm", "llm_dream", "portfolio_llm", "portfolio_dream", "portfolio_agent", "agent"):
        from .llm import LLMClient, load_endpoints
        llm_budget = getattr(a, "llm_budget", None) or 0
        try:
            llm_budget = llm_budget or int(float(os.environ.get("ANTS_LLM_BUDGET", 0)))
        except (ValueError, TypeError):
            llm_budget = 0
        client = LLMClient(load_endpoints(a.llm_env or _default_llm_env()), led,
                           budget=llm_budget, arm=a.arm)
    mem = (lambda arm: latest_memory(led, arm)) if a.arm in ("llm_dream", "portfolio_dream") else None
    p = make_proposer(a.arm, c, a.seed, client, mem)
    # --- definition guard: refuse when the arm definition changed unless --new-definition. Before the Campaign exists: a refused start must not
    #     register an epoch row in the ledger it refuses to touch. ---
    cur_def = Campaign.definition_of(a.arm, p, c)
    rc = _definition_guard(led, a.arm, cur_def, getattr(a, "new_definition", False))
    if rc is not None:
        lock.release()
        return rc
    camp = Campaign(led, c, wd, a.arm, p, a.seed, a.max_evals, a.max_cpu_s,
                    dream_every=a.dream_every if a.arm in ("llm_dream", "portfolio_dream") else 0,
                    dream_fn=(lambda: dream(led, c, a.arm, client)) if a.arm in ("llm_dream", "portfolio_dream") else None)
    stop = {"v": False}
    if client is not None:
        client.set_stop(lambda: stop["v"])        # backoff and further attempts of a blocked model call end with the daemon

    def on_signal(*_):
        stop["v"] = True                  # a running trial notices within ~0.25 s (run_command polls the flag) ...
        if camp.interruptible:            # ... and a blocking LLM call, with nothing in flight, is abandoned on the spot
            raise Shutdown()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, on_signal)
    # detect whether --new-definition was actually needed (hash mismatch that the guard allowed)
    _def_changed = False
    _prev_hash = None
    if getattr(a, "new_definition", False):
        starts = led.recent_events("daemon_started", 1)
        if starts:
            _prev_hash = starts[-1]["payload"].get("arm_def_hash")
            import hashlib as _h
            cur_hash = _h.sha256(json.dumps(camp.arm_definition(), sort_keys=True).encode()).hexdigest()[:12]
            _def_changed = _prev_hash is not None and _prev_hash != cur_hash
    print(camp.run(stop=lambda: stop["v"], definition_changed=_def_changed, prev_hash=_prev_hash), flush=True)
    if client is not None and threading.active_count() > 1:       # the LLM client's worker threads; a run without one has nothing to wait for
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
    from .home import find_catalog as locate_catalog      # the same search as data.locate_catalog, but a directory this user cannot enter is skipped
    if a.action == "fetch":
        from .fetch import fetch
        from .home import data_dir
        from .system import stage_data
        base = Path(a.catalog).parent if a.catalog else (data_dir() if (data_dir() / "sources.toml").exists() else locate_catalog().parent)
        if base == data_dir() or not (base / "sources.toml").exists():
            from .home import package_root
            stage_data(package_root(), data_dir())
            base = data_dir()
        rep = fetch(base, a.tier)
        print(f"{len(rep['fetched'])} fetched, {len(rep['have'])} already here, {len(rep['failed'])} failed, {len(rep['mismatch'])} rejected; "
              f"{len(rep['unpinned'])} file(s) have no pinned hash yet")
        return 0 if not (rep["failed"] or rep["mismatch"]) else 2
    cat = Catalog(a.catalog or locate_catalog())
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
        if a.present:                                   # only what has been downloaded (dev tier installs, partial tiers)
            names = [n for n in cat.names() if cat.meta[n]["format"] != "synth" and (cat.base / cat.meta[n]["raw"]).exists()]
        try:
            rep = cat.build_processed(names)
        except FileNotFoundError as e:                  # the catalog lists a dataset this install never fetched
            print(f"ants data build: {e.filename or e} is not there: the catalog lists datasets this install does not have.\n"
                  f"  build the ones that are present:  ants data build --present\n"
                  f"  fetch every dataset:              scripts/fetch_data.sh all", file=sys.stderr)
            return 2
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
    h = History.for_arm(led, c, a.arm)
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
    h = History.for_arm(led, c, a.arm); h.update()
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


def cmd_diff(a):
    from .workspace import diff_trials
    wd, led, c = _open(a)
    d = diff_trials(led, c, a.a, a.b)
    _emit(a, d, lambda x: x["not_found"] if "not_found" in x else
          f"trial {x['a']} vs {x['b']}\n" + "\n".join(f"  {k}: {v[0]} -> {v[1]}" for k, v in x["levers"].items()) + "\n"
          + "\n".join(f"  {s} {c.primary}: {v[0]} vs {v[1]}" for s, v in x["stages"].items()))
    return 1 if "not_found" in d else 0


def cmd_workspace(a):
    from .workspace import export_workspace
    wd, led, c = _open(a)
    r = export_workspace(led, c, a.arm, a.out, a.limit)
    print(f"wrote {r['trials']} trials ({r['detailed']} with detail) to {r['out']}; start with {r['out']}/SKILL.md")
    return 0


def cmd_epochs(a):
    from . import ui
    wd, led, c = _open(a)
    rows = ui.epochs(led, c)
    def text(rs):
        import time as _t
        f = lambda t: _t.strftime("%m-%d %H:%M", _t.localtime(t)) if t else "-"
        cur = led.conn.execute("SELECT epoch FROM trials ORDER BY id DESC LIMIT 1").fetchone()
        return "\n".join(f"{'*' if cur and r['epoch'] == cur[0] else ' '} {r['epoch']:<14} {r['trials']:>7} trials {r['done']:>7} done  {f(r['first'])} .. {f(r['last'])}  ants {', '.join(map(str, r['versions'])) or '?'}" for r in rs) + "\n(* = the epoch the search is in now; results of different epochs are never pooled)"
    _emit(a, rows, text)
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
    from .system import durability_checks
    from .update import current_version, is_newer, read_cache
    res = res + durability_checks(led, wd)
    known = read_cache().get("latest")
    res.append(("WARN", f"ants {known} is available (running {current_version()}); `ants update`") if known and is_newer(known, current_version())
               else ("OK", f"ants {current_version()}" + (f" (latest known {known})" if known else "")))
    _emit(a, [{"level": l, "message": m} for l, m in res], lambda d: "\n".join(f"[{x['level']:>4}] {x['message']}" for x in d))
    return 2 if any(l == "FAIL" for l, _ in res) else 0


SHELL_COMMANDS = ("top", "best", "show", "diff", "log", "hint", "pause", "resume", "doctor", "status", "export", "board")


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


def cmd_ui(a):
    from .tui.app import run
    return run(contract=a.contract, workdir=a.workdir, arm=a.arm or None)


def cmd_home(a):
    from .tui.app import once
    print(once(contract=a.contract, workdir=a.workdir, arm=a.arm or None, width=a.width, screen=a.screen, color=True if a.color else None))
    return 0


def _can_tui() -> bool:
    try:
        import curses  # noqa: F401
    except ImportError:
        return False
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM", "") not in ("", "dumb") and not os.environ.get("ANTS_NO_TUI")


def _first_run_done() -> bool:
    from .home import config_path
    if config_path().exists() or os.environ.get("ANTS_WORKDIR") or os.environ.get("ANTS_CONTRACT"):
        return True
    # The entry gate and viewer must agree about series and migrated workdirs.
    from .tui.model import discover_workdirs
    if discover_workdirs():
        return True
    # An archive-only installation is still useful, including when its cache
    # needs repair. Show the read-only error, never offer an unrelated download.
    from .archive_index import index_path
    try:
        return index_path().is_file()
    except OSError:
        return False


def _first_run(ap) -> int:
    """`ants` with nothing set up yet: offer onboarding on a terminal, explain it otherwise (never download unasked)"""
    from .home import home
    msg = f"ants is not set up yet. `ants onboard` creates {home()}, downloads the public datasets (about 50 MB for the dev tier) and writes the config."
    if sys.stdin.isatty() and sys.stdout.isatty():
        print(msg)
        try:
            answer = input("Set up now? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if answer in ("", "y", "yes"):
            return ap.parse_args(["onboard"]).fn(ap.parse_args(["onboard"]))
        return 0
    print(msg, file=sys.stderr)
    return 0


def _system_parsers(sub) -> None:
    from . import system
    def common(s, needs_workdir=True):
        s.add_argument("--workdir"); s.add_argument("--json", action="store_true")
        s.set_defaults(needs_workdir=needs_workdir)
    s = sub.add_parser("onboard", help="create ~/.ants, fetch datasets, write the config")
    s.add_argument("--tier", default="dev"); s.add_argument("--no-fetch", action="store_true")
    s.add_argument("--contract"); s.add_argument("--workdir"); s.add_argument("--arm", default=None)
    s.set_defaults(fn=system.cmd_onboard)
    s = sub.add_parser("backup", help="verified online snapshot of the ledger")
    common(s); s.add_argument("--dest"); s.add_argument("--keep", type=int, default=14); s.add_argument("--gzip", action="store_true")
    s.set_defaults(fn=system.cmd_backup)
    s = sub.add_parser("backups", help="list snapshots")
    common(s); s.add_argument("--dest"); s.set_defaults(fn=system.cmd_backups)
    s = sub.add_parser("restore", help="verify a snapshot and put it in place (the daemon must be stopped)")
    common(s); s.add_argument("which", nargs="?", help="file name or timestamp prefix; default the newest")
    s.add_argument("--dest"); s.add_argument("--force", action="store_true", help="move an existing ledger aside and restore over it")
    s.add_argument("--allow-different", action="store_true", help="also accept a snapshot of another ledger, or one older than the current ledger")
    s.set_defaults(fn=system.cmd_restore)
    for name in ("sql", "query"):
        s = sub.add_parser(name, help="read-only SELECT over the ledger")
        common(s); s.add_argument("sql", nargs="?"); s.add_argument("--schema", action="store_true")
        s.add_argument("--format", choices=["table", "json", "jsonl", "csv"], default="table"); s.add_argument("--limit", type=int, default=1000)
        s.set_defaults(fn=system.cmd_sql)
    s = sub.add_parser("update", help="look for a newer ants; --apply installs it")
    s.add_argument("--apply", action="store_true"); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=lambda a: __import__("antslab.update", fromlist=["cmd_update"]).cmd_update(a))
    s = sub.add_parser("service", help="keep `ants run` resident (systemd user unit or launchd agent)")
    s.add_argument("action", choices=["install", "uninstall", "status", "logs", "restart"])
    s.add_argument("--name", default="default"); s.add_argument("--contract"); s.add_argument("--workdir"); s.add_argument("--arm", default=None)
    s.add_argument("--seed", type=int, default=1); s.add_argument("--llm-env"); s.add_argument("--now", action="store_true", help="install: also start it")
    s.add_argument("--print", action="store_true", help="install: show the unit files and write nothing")
    s.add_argument("-n", "--lines", type=int, default=50); s.add_argument("-f", "--follow", action="store_true")
    s.set_defaults(fn=_cmd_service)


def _cmd_service(a):
    from . import system
    if a.action == "install":
        _resolve(a)
        a.workdir = str(Path(a.workdir).expanduser().resolve())
        a.contract = str(Path(a.contract).expanduser().resolve())
    return system.cmd_service(a)


def cmd_experiments(a):
    from .experiments import load_experiments, check_experiment, RegistryError
    try:
        exps = load_experiments(a.file)
    except RegistryError as e:
        print(f"ants experiments: {e}", file=sys.stderr)
        return 2
    results = []
    for exp in exps:
        r = check_experiment(exp, a.base)
        results.append(r)
    if a.json:
        print(json.dumps(results, indent=1))
    else:
        for r in results:
            ledger = "ledger found" if r["ledger_exists"] else "no ledger"
            match_str = ""
            if r["match"] is True:
                match_str = "hash ok"
            elif r["match"] is False:
                match_str = f"MISMATCH (ledger {r['last_hash']}, code {r['current_hash']})"
            elif r["last_hash"] is None and r["ledger_exists"]:
                match_str = "no daemon_started yet"
            elif not r["ledger_exists"]:
                match_str = "-"
            print(f"  {r['name']:<24} arm={r['arm']:<20} seed={r['seed']}  status={r['status']:<10}  {ledger}  {match_str}")
    return 0


def cmd_seeds(a):
    from .seeds import run_seeds
    return run_seeds(a)


def cmd_series(a):
    from .series import run_series, series_status, series_check
    action = a.action
    if action == "run":
        return run_series(a)
    elif action == "status":
        return series_status(a)
    elif action == "check":
        return series_check(a)
    print(f"ants series: unknown action {action!r}", file=sys.stderr)
    return 1


def cmd_bundle(a):
    from .bundle import make_bundle, verify_bundle, BundleError
    action = a.action
    if action == "make":
        if not a.workdirs:
            print("ants bundle make: at least one WORKDIR required", file=sys.stderr)
            return 1
        try:
            make_bundle(a.name, a.out, a.workdirs, note_file=a.note)
        except BundleError as e:
            print(f"ants bundle make: {e}", file=sys.stderr)
            return 1
        return 0
    elif action == "verify":
        if not a.path:
            print("ants bundle verify: PATH required", file=sys.stderr)
            return 1
        return verify_bundle(a.path)
    print(f"ants bundle: unknown action {action!r}", file=sys.stderr)
    return 1


def cmd_archive(a):
    from .archive_index import build_index, read_index
    try:
        if a.action == "index":
            result = build_index(a.bundle, _contract_path(a.contract), a.cache,
                                 a.title, a.kind, a.offsite_note, a.location)
        else:
            result = read_index(Path(a.cache) / "index.json" if a.cache else None)
        print(json.dumps(result, indent=2))
        return 1 if result.get("error") else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ants archive: {exc}", file=sys.stderr)
        return 1


def cmd_preflight(a):
    from .preflight import run_preflight
    return run_preflight(
        series_name=a.series, series_file=getattr(a, "series_file", None),
        workdir=a.workdir, arm=a.arm, base=a.base,
        contract=a.contract, llm_env=getattr(a, "llm_env", None),
        as_json=a.json, strict=a.strict)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ants")
    ap.add_argument("--version", action="version", version="ants " + _version())
    sub = ap.add_subparsers(dest="cmd", required=False)
    for name, fn in (("run", cmd_run), ("status", cmd_status), ("export", cmd_export), ("janitor", cmd_janitor), ("board", cmd_board), ("serve", cmd_serve),
                     ("top", cmd_top), ("best", cmd_best), ("show", cmd_show), ("diff", cmd_diff), ("workspace", cmd_workspace), ("epochs", cmd_epochs), ("log", cmd_log), ("hint", cmd_hint),
                     ("pause", cmd_pause), ("resume", cmd_pause), ("doctor", cmd_doctor), ("shell", cmd_shell)):
        s = sub.add_parser(name)
        s.add_argument("--contract"); s.add_argument("--workdir")
        s.add_argument("--arm", default=None); s.add_argument("--seed", type=int, default=0)
        s.set_defaults(fn=fn)
        if name == "run":
            s.add_argument("--max-evals", type=int); s.add_argument("--max-cpu-s", type=float)
            s.add_argument("--llm-env", default=None, help="env file with LLM_<TAG>_BASE/KEY/MODEL (default ~/.config/ants/llm.env)")
            s.add_argument("--llm-budget", type=int, default=0, help="total HTTP attempts the arm may spend over its whole life (0 = none; env ANTS_LLM_BUDGET)")
            s.add_argument("--dream-every", type=int, default=100)
            s.add_argument("--new-definition", action="store_true", help="proceed even when the arm definition hash differs from the ledger's last start")
        if name == "export":
            s.add_argument("--out", required=True)
        s.add_argument("--json", action="store_true", help="machine-readable output for other agents")
        if name == "top":
            s.add_argument("--every", type=float, default=5); s.add_argument("--once", action="store_true")
        if name == "best":
            s.add_argument("--top", type=int, default=5); s.add_argument("--cap", type=int)
        if name == "show":
            s.add_argument("ref", help="trial id or spec-key prefix")
        if name == "diff":
            s.add_argument("a"); s.add_argument("b")
        if name == "workspace":
            s.add_argument("--out", required=True); s.add_argument("--limit", type=int, default=3000)
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
    d = sub.add_parser("data"); d.add_argument("action", choices=["list", "verify", "build", "fetch"])
    d.add_argument("--catalog", default=None); d.add_argument("--tier", default="dev", help="fetch: dev | standard | extended | all"); d.add_argument("--names"); d.add_argument("--present", action="store_true", help="build: every real dataset whose raw files exist"); d.add_argument("--all", action="store_true"); d.add_argument("--partial", action="store_true", help="verify only the files that exist (dev-tier installs)")
    d.set_defaults(fn=cmd_data)
    _system_parsers(sub)
    # --- experiments registry ---
    s = sub.add_parser("experiments", help="list experiments from deploy/experiments.toml and check their ledgers")
    s.add_argument("--file", default="deploy/experiments.toml", help="path to experiments.toml")
    s.add_argument("--base", default="/var/lib/ants", help="base directory where workdirs live")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_experiments)
    # --- seed runner ---
    s = sub.add_parser("seeds", help="run multiple seeds of an arm")
    s.add_argument("--arm", required=True, help="arm name(s), comma-separated")
    s.add_argument("--seeds", required=True, help="seed range (1-8) or list (1,2,5)")
    s.add_argument("--base", required=True, help="base directory for workdirs")
    s.add_argument("--contract", default=None); s.add_argument("--max-cpu-s", type=float, default=None)
    s.add_argument("--max-evals", type=int, default=None); s.add_argument("--workers", type=int, default=1)
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--llm-env", default=None)
    s.add_argument("--allow-with-arms", action="store_true", help="start although ants@<arm> services are running on this host")
    s.add_argument("--allow-model-arms", action="store_true", help="allow arms that call a model (the daily cap is per ledger, so per seed)")
    s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_seeds)
    # --- series ---
    sp = sub.add_parser("series", help="manage a pre-registered series of seed runs")
    sp_sub = sp.add_subparsers(dest="action", required=True)
    for act in ("run", "status", "check"):
        ss = sp_sub.add_parser(act)
        ss.add_argument("name", help="series name (matches deploy/series/<name>.toml)")
        ss.add_argument("--file", default=None, help="explicit path to the series TOML file")
        ss.add_argument("--base", default=None, help="override base directory")
        if act == "run":
            ss.add_argument("--allow-with-arms", action="store_true", help="start although ants@<arm> services are running on this host")
            ss.add_argument("--dry-run", action="store_true")
            ss.add_argument("--llm-env", default=None)
        if act == "status":
            ss.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_series)
    # --- bundle ---
    bp = sub.add_parser("bundle", help="create or verify a sealed results bundle")
    bp_sub = bp.add_subparsers(dest="action", required=True)
    bm = bp_sub.add_parser("make")
    bm.add_argument("--name", required=True, help="bundle name")
    bm.add_argument("--out", required=True, help="output directory")
    bm.add_argument("--note", default=None, help="path to a note file to include in SEAL.md")
    bm.add_argument("workdirs", nargs="*", help="workdir paths to include")
    bv = bp_sub.add_parser("verify")
    bv.add_argument("path", help="path to the bundle directory or .tar.gz")
    bp.set_defaults(fn=cmd_bundle)
    ar = sub.add_parser("archive", help="index and browse verified, cached research rounds")
    ars = ar.add_subparsers(dest="action", required=True)
    ai = ars.add_parser("index", help="verify a sealed bundle and append its immutable summary")
    ai.add_argument("--bundle", required=True); ai.add_argument("--contract", required=True)
    ai.add_argument("--cache", required=True); ai.add_argument("--title", default="")
    ai.add_argument("--kind", choices=["pilot", "completed", "aborted", "snapshot"], default="snapshot")
    ai.add_argument("--offsite-note", default="", help="operator-recorded off-host verification receipt, not a live check")
    ai.add_argument("--location", default="", help="display location of the original sealed bundle")
    al = ars.add_parser("list", help="read the cached round index without opening ledgers")
    al.add_argument("--cache", default=None)
    ar.set_defaults(fn=cmd_archive)
    # --- preflight ---
    pf = sub.add_parser("preflight", help="read-only pre/post-deploy checks")
    pf.add_argument("--series", default=None, help="series name (loads deploy/series/<name>.toml)")
    pf.add_argument("--series-file", default=None, help="explicit path to series TOML")
    pf.add_argument("--workdir", default=None); pf.add_argument("--arm", default=None)
    pf.add_argument("--base", default=None); pf.add_argument("--contract", default=None)
    pf.add_argument("--llm-env", default=None); pf.add_argument("--json", action="store_true")
    pf.add_argument("--strict", action="store_true", help="exit 1 on WARN too")
    pf.set_defaults(fn=cmd_preflight)
    s = sub.add_parser("ui", help="the terminal interface (what plain `ants` opens on a terminal)")
    s.add_argument("--contract"); s.add_argument("--workdir"); s.add_argument("--arm", default=None); s.set_defaults(fn=cmd_ui)
    s = sub.add_parser("home", help="print one screen of the interface and exit (pipes, scripts)")
    s.add_argument("--contract"); s.add_argument("--workdir"); s.add_argument("--arm", default=None)
    s.add_argument("--screen", default="home", choices=["home", "overview", "board", "trials", "fleet", "events", "health", "data", "system", "past", "afterlife"])
    s.add_argument("--width", type=int, default=100); s.add_argument("--color", action="store_true"); s.set_defaults(fn=cmd_home)
    a = ap.parse_args(argv)
    if a.cmd is None:                                 # plain `ants`: set up on the first run, else the interactive shell
        if not _first_run_done():
            return _first_run(ap)
        a = ap.parse_args(["ui" if _can_tui() else "shell"])        # a terminal gets the interface; a pipe or a dumb terminal the line shell
    if a.fn in (cmd_shell, cmd_top, cmd_best, cmd_status):
        from .update import notice
        line = notice()
        if line:
            print(line, file=sys.stderr)
    if getattr(a, "needs_workdir", False):
        _resolve_workdir(a)
        a.workdir = str(Path(a.workdir).expanduser().resolve())
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

"""Read-only data access for the terminal interface. One `Source` per workdir; one function per screen.

Nothing here writes: ledgers are opened with `Ledger.open_readonly`, so a viewer on a live server never takes a lock the daemon wants.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from .. import ui
from ..contract import load_contract
from ..history import History
from ..ledger import Ledger

CONTRACT_SEARCH = ("/opt/ants/contracts/tinyts.toml", "contracts/tinyts_real.toml", "contracts/tinyts_synth_hard.toml")


def discover_workdirs(extra=()) -> list[Path]:
    """every directory that holds a ledger and is reachable the way the CLI looks: flag, environment, config, the service area, ~/.ants/runs, ./runs"""
    from ..home import home, load_config
    cands = [Path(p) for p in extra if p]
    if os.environ.get("ANTS_WORKDIR"):
        cands.append(Path(os.environ["ANTS_WORKDIR"]))
    cfg = load_config().get("run", {}).get("workdir")
    if cfg:
        cands.append(Path(cfg))
    from ..home import ledgers_in
    for base in (Path("/var/lib/ants"), home() / "runs", Path("runs")):
        cands += sorted(p.parent for p in ledgers_in(base))
    out, seen = [], set()
    for p in cands:
        p = p.expanduser()
        try:
            key = p.resolve()
        except OSError:
            continue
        try:
            present = (p / "ledger.sqlite").exists()
        except OSError:
            present = False
        if key not in seen and present:
            seen.add(key)
            out.append(p)
    return out


def find_contract(explicit: str | None = None) -> str:
    from ..home import load_config
    for c in (explicit, os.environ.get("ANTS_CONTRACT"), load_config().get("run", {}).get("contract"), *CONTRACT_SEARCH):
        if c and Path(c).exists():
            return str(c)
        root = os.environ.get("ANTS_ROOT")
        if c and root and (Path(root) / "contracts" / f"{c}.toml").exists():
            return str(Path(root) / "contracts" / f"{c}.toml")
    from ..home import package_root
    root = os.environ.get("ANTS_ROOT") or package_root()
    if root and (Path(root) / "contracts" / "tinyts_real.toml").exists():
        return str(Path(root) / "contracts" / "tinyts_real.toml")
    raise FileNotFoundError("no contract found: run `ants onboard` or pass --contract")


class Source:
    def __init__(self, workdir, contract_path: str, arm: str | None = None):
        self.workdir = Path(workdir)
        self.contract_path = contract_path
        self.c = load_contract(contract_path)
        self.led = Ledger.open_readonly(self.workdir / "ledger.sqlite")
        from ..textsafe import clean_text
        self.arm = clean_text(arm or self._latest_arm(), 60)
        self.hist = History.for_arm(self.led, self.c, self.arm)

    def _latest_arm(self) -> str:
        r = self.led.conn.execute("SELECT arm FROM trials ORDER BY id DESC LIMIT 1").fetchone()
        return r[0] if r else "portfolio"

    def close(self):
        self.led.close()

    # ---- screens --------------------------------------------------------
    def overview(self) -> dict:
        snap = ui.snapshot(self.led, self.c, self.arm, self.workdir, self.hist)
        now = time.time()
        step = 600
        rows = self.led.conn.execute("SELECT CAST((? - finished_at)/? AS INTEGER) b, COUNT(*) FROM trials WHERE gym=? AND arm=? AND finished_at>? GROUP BY b",
                                     (now, step, self.c.gym, self.arm, now - 6 * 3600)).fetchall()
        by = {r[0]: r[1] for r in rows}
        snap["rate_6h"] = [by.get(i, 0) for i in range(35, -1, -1)]                 # finished per 10 minutes, oldest first
        snap["totals"] = {r[0]: r[1] for r in self.led.conn.execute("SELECT state, COUNT(*) FROM trials WHERE gym=? AND arm=? AND epoch IS COALESCE(?, epoch) GROUP BY state", (self.c.gym, self.arm, self.hist.epoch))}
        timeline = self.best_timeline()
        snap["curve"] = self.thin([v for _, v in timeline])
        snap["running"] = self.running(now)
        snap["events_min"] = self.events_per_minute(now)
        snap["calls_hour"] = self.calls_per_hour(now)
        snap["mood"] = self.mood_inputs(snap, timeline, now)
        return snap

    @staticmethod
    def thin(out: list, points: int = 40) -> list:
        if len(out) > points:
            step = len(out) / points
            out = [out[int(i * step)] for i in range(points - 1)] + [out[-1]]
        return out

    def best_timeline(self) -> list[tuple]:
        """(finish time, best-so-far) for every deepest-stage evaluation, in finish order"""
        last = self.c.stages[-1]
        sign = self.hist.sign
        best, out = None, []
        for t, v in self.led.conn.execute(
                "SELECT finished_at, json_extract(metrics, ?) FROM trials WHERE gym=? AND arm=? AND epoch IS COALESCE(?, epoch) AND stage=? AND state='done' AND metrics IS NOT NULL ORDER BY finished_at, id",
                (f"$.{self.c.primary}", self.c.gym, self.arm, self.hist.epoch, last)):
            if v is None:
                continue
            best = v if best is None or sign * v < sign * best else best
            out.append((t, best))
        return out

    def running(self, now: float) -> list[dict]:
        rows = self.led.conn.execute("SELECT id, stage, source, started_at FROM trials WHERE gym=? AND arm=? AND state='running' ORDER BY started_at", (self.c.gym, self.arm)).fetchall()
        from ..textsafe import clean_text
        return [{"id": r[0], "stage": r[1], "source": clean_text(r[2], 40), "for_s": now - r[3] if r[3] else None} for r in rows]

    def events_per_minute(self, now: float, minutes: int = 60) -> list[int]:
        by = {r[0]: r[1] for r in self.led.conn.execute("SELECT CAST((? - ts)/60 AS INTEGER) b, COUNT(*) FROM events WHERE ts>? GROUP BY b", (now, now - minutes * 60))}
        return [by.get(i, 0) for i in range(minutes - 1, -1, -1)]

    def calls_per_hour(self, now: float, hours: int = 24) -> list[tuple]:
        """(ok, failed) model calls per hour, oldest first"""
        ok, bad = {}, {}
        for b, good, n in self.led.conn.execute("SELECT CAST((? - ts)/3600 AS INTEGER) b, ok, COUNT(*) FROM llm_calls WHERE ts>? GROUP BY b, ok", (now, now - hours * 3600)):
            (ok if good else bad)[b] = n
        return [(ok.get(i, 0), bad.get(i, 0)) for i in range(hours - 1, -1, -1)]

    def mood_inputs(self, snap: dict, timeline: list, now: float) -> dict:
        import statistics
        rate = snap["rate_6h"]
        fin = [r[0] for r in self.led.conn.execute("SELECT state FROM trials WHERE gym=? AND arm=? AND state IN ('done','failed','timeout','crashed') ORDER BY finished_at DESC LIMIT 40", (self.c.gym, self.arm))]
        improved = None
        prev = None
        for t, v in timeline:
            if prev is None or self.hist.sign * v < self.hist.sign * prev:
                improved = t
            prev = v
        calls = self.led.conn.execute("SELECT COUNT(*), COALESCE(SUM(ok),0) FROM llm_calls WHERE ts>?", (now - 3600,)).fetchone()
        backup_age = None
        try:
            from .. import system
            from ..backup import list_backups
            d = system.backup_dir(self.workdir)
            bk = list_backups(d) if d.exists() else []
            backup_age = (now - bk[0]["created"]) / 3600 if bk else None
        except Exception:
            pass
        return {"paused": snap["paused"], "chain_ok": snap["chain_tail_ok"], "free_gb": snap["free_gb"], "evals": snap["totals"].get("done", 0),
                "finished_1h": snap["finished_1h"], "rate_median_1h": (statistics.median(rate) * 6) if rate else 0, "last_age_s": snap["last_finish_age_s"],
                "fail_share": (sum(1 for s in fin if s != "done") / len(fin)) if fin else 0.0,
                "best_age_s": (now - improved) if improved else None,
                "llm_fail_share": ((calls[0] - calls[1]) / calls[0]) if calls[0] >= 4 else None, "backup_age_h": backup_age}

    def best_curve(self, points: int = 40) -> list[float]:
        """best deepest-stage primary value over time (finish order), thinned to `points`: how the search is going at a glance (kept for callers)"""
        last = self.c.stages[-1]
        sign = self.hist.sign
        vals = [(r[0], r[1]) for r in self.led.conn.execute(
            "SELECT finished_at, json_extract(metrics, ?) FROM trials WHERE gym=? AND arm=? AND epoch IS COALESCE(?, epoch) AND stage=? AND state='done' AND metrics IS NOT NULL ORDER BY finished_at",
            (f"$.{self.c.primary}", self.c.gym, self.arm, self.hist.epoch, last)) if r[1] is not None]
        best, out = None, []
        for _, v in vals:
            best = v if best is None or sign * v < sign * best else best
            out.append(best)
        if len(out) > points:
            step = len(out) / points
            out = [out[int(i * step)] for i in range(points - 1)] + [out[-1]]
        return out

    def leaderboard(self, top: int = 10) -> dict:
        return ui.leaderboard(self.hist, self.c, top)

    def trials(self, state: str | None = None, stage: str | None = None, limit: int = 300) -> list[dict]:
        q = "SELECT id, stage, state, source, spec_key, cpu_s, finished_at, json_extract(metrics, ?) AS value, error FROM trials WHERE gym=? AND arm=?"
        args: list = [f"$.{self.c.primary}", self.c.gym, self.arm]
        if state:
            q += " AND state=?"; args.append(state)
        if stage:
            q += " AND stage=?"; args.append(stage)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        from ..textsafe import clean_text
        out = []
        for r in self.led.conn.execute(q, args):
            d = dict(r)
            d["source"], d["state"] = clean_text(d["source"], 60), clean_text(d["state"], 20)     # anything a gym wrote reaches the screen cleaned
            d["error"] = clean_text(d["error"], 300) if d["error"] else d["error"]
            out.append(d)
        return out

    def detail(self, tid: int) -> dict:
        return ui.show(self.led, self.c, str(tid))

    def events(self, n: int = 200, kind: str | None = None) -> list[dict]:
        return ui.log(self.led, n, kind)

    def health_extra(self) -> dict:
        import shutil
        out = {}
        try:
            du = shutil.disk_usage(self.workdir)
            out["disk_used_frac"] = du.used / du.total
        except OSError:
            pass
        try:
            out["ledger_mb"] = (self.workdir / "ledger.sqlite").stat().st_size / 1e6
            from .. import system
            from ..backup import list_backups
            d = system.backup_dir(self.workdir)
            bk = list_backups(d) if d.exists() else []
            out["backup_age_h"] = (time.time() - bk[0]["created"]) / 3600 if bk else None
        except Exception:
            pass
        return out

    def health(self) -> list[tuple[str, str]]:
        from ..system import durability_checks
        res = ui.doctor(self.led, self.c, self.arm, self.workdir)
        try:
            res = res + durability_checks(self.led, self.workdir)
        except Exception as e:
            res.append(("WARN", f"durability checks failed: {type(e).__name__}"))
        return res


def deep_series(src: "Source") -> list[tuple]:
    """(finish time, value, size) of every finished evaluation at the deepest stage, in finish order: the unit arms are compared in"""
    c = src.c
    q = ("SELECT finished_at, json_extract(metrics, ?), " + (f"json_extract(metrics, '$.{c.cap_metric}')" if c.cap_metric else "NULL") +
         " FROM trials WHERE gym=? AND arm=? AND epoch IS COALESCE(?, epoch) AND stage=? AND state='done' AND metrics IS NOT NULL ORDER BY finished_at, id")
    return [(r[0], r[1], r[2]) for r in src.led.conn.execute(q, (f"$.{c.primary}", c.gym, src.arm, src.hist.epoch, c.stages[-1])) if r[1] is not None]


def best_at(src: "Source", n: int) -> dict:
    """best value per size cap among the first `n` deepest-stage evaluations (cap N holds every model of at most N parameters)"""
    c = src.c
    best: dict = {}
    for _, v, size in deep_series(src)[:n]:
        bins = [str(cap) for cap in c.caps if size is not None and size <= cap] if c.caps else ["all"]
        for b in bins:                                   # cumulative: a model of 90 parameters also counts under <=300
            if b not in best or src.hist.sign * v < src.hist.sign * best[b]:
                best[b] = v
    return best


def equal_budget(sources: list["Source"]) -> dict:
    """the comparison that means something: every arm cut at the same number of deepest-stage evaluations (the smallest any of them has)"""
    series = {id(s): deep_series(s) for s in sources}
    n = min((len(v) for v in series.values() if v), default=0)         # an arm with nothing at the deepest stage yet does not pull everyone to zero
    rows = []
    for s in sources:
        rows.append({"arm": s.arm if s.workdir.name == s.arm else f"{s.workdir.name}/{s.arm}", "best": best_at(s, n) if n and series[id(s)] else {}, "n": len(series[id(s)])})
    return {"n": n, "rows": rows}


def fleet(sources: list["Source"]) -> list[dict]:
    """one line per workdir/arm for the home screen and the comparison screen"""
    out = []
    for s in sources:
        try:
            snap = s.overview()
        except Exception as e:                       # a ledger that cannot be read must not blank the screen
            out.append({"workdir": str(s.workdir), "arm": s.arm if s.workdir.name == s.arm else f"{s.workdir.name}/{s.arm}", "error": f"{type(e).__name__}: {str(e)[:80]}"})
            continue
        done = snap["totals"].get("done", 0)
        deepest = s.c.stages[-1]
        n_deep = snap["stages"].get(deepest, {}).get("done", 0)
        out.append({"workdir": str(s.workdir), "arm_name": s.arm, "arm": s.arm if s.workdir.name == s.arm else f"{s.workdir.name}/{s.arm}", "epoch": snap["epoch"], "evals": done, "deep": n_deep, "h1": snap["finished_1h"],
                    "last_age": snap["last_finish_age_s"], "paused": snap["paused"], "chain": snap["chain_tail_ok"], "mood": snap["mood"], "rate_6h": snap["rate_6h"], "ops": snap["operators"],
                    "best": {k: v[0] for k, v in snap["best_by_cap"].items() if v}, "free_gb": snap["free_gb"], "curve": snap["curve"]})
    return out


def _ok(path) -> bool:
    """exists() that answers False instead of raising when a parent directory cannot be entered"""
    try:
        return path.exists()
    except OSError:
        return False


def data_overview(catalog_path: str | None = None) -> dict:
    from ..data import Catalog
    from ..home import find_catalog
    cat = Catalog(catalog_path or find_catalog())
    roles: dict = {}
    for n in cat.names():
        m = cat.meta[n]
        if m["format"] == "synth":
            roles.setdefault("synthetic", []).append(n)
            continue
        roles.setdefault(m["role"], []).append((n, _ok(cat.base / m["raw"])))
    try:
        size = sum(f.stat().st_size for f in (cat.base / "raw").rglob("*") if f.is_file()) if _ok(cat.base / "raw") else 0
    except OSError:
        size = 0
    try:
        processed = len(list((cat.base / "processed").glob("*.npz"))) if _ok(cat.base / "processed") else 0
    except OSError:
        processed = 0
    return {"base": str(cat.base), "roles": roles, "raw_bytes": size, "processed": processed}


def human_error(e: BaseException, what: str = "this screen") -> str:
    """An exception as a short explanation a person can act on (lines separated by newlines). Never suggests making data readable."""
    import getpass
    from ..home import CatalogUnreadable
    try:
        user = getpass.getuser()
    except Exception:
        user = "this user"
    if isinstance(e, (CatalogUnreadable, PermissionError)):
        path = getattr(e, "filename", None) or (e.paths[0] if getattr(e, "paths", None) else "")
        return (f"{what} cannot read {path or 'a file it needs'} as user '{user}' (permission denied).\n"
                "This is expected when the data belongs to another user: confirm and sealed data are kept apart on purpose, and nothing was made readable.\n"
                "Run `ants` as the user that owns the data, or set ANTS_CATALOG to a catalog this user can read (the installed one is /opt/ants/data/catalog.toml on the server).\n"
                "`ants doctor` says which user and which path.")
    if isinstance(e, FileNotFoundError):
        return f"{what} cannot find a file it needs: {e}\nRun `ants onboard`, or set ANTS_CATALOG / ANTS_HOME."
    return f"{what} could not be shown: {type(e).__name__}: {e}"


def system_info(source: "Source | None", all_sources: "list[Source] | None" = None) -> dict:
    import subprocess
    from ..home import config_path, home
    from ..update import current_version, is_newer, read_cache
    from .. import system
    info = {"version": current_version(), "home": str(home()), "config": str(config_path()) if config_path().exists() else None}
    cache = read_cache()
    info["latest"] = cache.get("latest")
    info["update"] = bool(cache.get("latest")) and is_newer(cache["latest"], info["version"])
    if source is not None:
        info["workdir"] = str(source.workdir)
        info["backup_dir"] = str(system.backup_dir(source.workdir))
        try:
            from ..backup import list_backups
            info["backups"] = list_backups(info["backup_dir"]) if Path(info["backup_dir"]).exists() else []
        except Exception:
            info["backups"] = []
        try:
            info["trials_now"] = source.led.conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]       # for "how much would a restore lose"
        except Exception:
            info["trials_now"] = None
        calls = source.led.conn.execute("SELECT COUNT(*), COALESCE(SUM(ok),0) FROM llm_calls WHERE ts>?", (time.time() - 86400,)).fetchone()
        from ..llm import daily_cap
        info["llm_24h"] = {"calls": calls[0], "ok": calls[1], "cap": daily_cap()}
        env = os.environ.get("ANTS_LLM_MAX_CALLS_PER_DAY")
        info["llm_cap_source"] = f"ANTS_LLM_MAX_CALLS_PER_DAY={env} in this shell" if env is not None else "built-in default (ANTS_LLM_MAX_CALLS_PER_DAY is not set in this shell; the running service reads its own environment)"
        per = []
        for o in (all_sources or [source]):                       # the counter lives in each ledger: the cap applies to each search separately
            try:
                r = o.led.conn.execute("SELECT COUNT(*), COALESCE(SUM(ok),0) FROM llm_calls WHERE ts>?", (time.time() - 86400,)).fetchone()
                per.append({"name": f"{o.workdir.name}/{o.arm}", "calls": r[0], "ok": r[1]})
            except Exception:
                per.append({"name": f"{o.workdir.name}/{o.arm}", "calls": None, "ok": None})
        info["llm_by_ledger"] = per
        info["agent_tools_24h"] = source.led.conn.execute("SELECT COUNT(*) FROM events WHERE kind='agent_tool' AND ts>?", (time.time() - 86400,)).fetchone()[0]
        info["calls_hour"] = source.calls_per_hour(time.time())
        info["now"] = time.time()
        # llm_summary for per-endpoint and per-arm detail
        try:
            info["llm_summary"] = source.led.llm_summary(time.time() - 86400)
        except Exception:
            info["llm_summary"] = None
        # endpoint quarantine state from recent events
        try:
            qevents = source.led.conn.execute(
                "SELECT payload FROM events WHERE kind='llm_endpoint_state' ORDER BY seq DESC LIMIT 20").fetchall()
            import json as _json
            ep_states: dict = {}
            for row in qevents:
                p = _json.loads(row[0])
                ep = p.get("endpoint", "")
                if ep and ep not in ep_states:
                    ep_states[ep] = p
            info["llm_ep_states"] = ep_states
        except Exception:
            info["llm_ep_states"] = {}
    try:
        unit = f"ants@{source.arm}" if source and Path("/etc/systemd/system/ants@.service").exists() else None
        if unit:
            r = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=3)
            info["service"] = {"unit": unit, "state": r.stdout.strip() or r.stderr.strip()}
    except (OSError, subprocess.SubprocessError):
        pass
    return info

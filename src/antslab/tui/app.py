"""The curses loop. Data is loaded by one background thread (so a slow ledger never freezes the keys); the UI thread only draws the last
result and handles keys. Every screen is drawn by views.py; this file knows about keys, colours and the command line, nothing else."""
from __future__ import annotations

import contextlib
import io
import os
import shlex
import sys
import threading
import time
from pathlib import Path

from . import anim, model, render, views
from .views import Ctx, S

REFRESH_S = 3.0
PALETTE_COMMANDS = {"best", "show", "diff", "log", "doctor", "status", "sql", "query", "backups", "hint", "pause", "resume"}   # read-only, plus the three that write one small thing
NEEDS_CONTEXT = {"best", "show", "diff", "log", "doctor", "status", "hint", "pause", "resume"}   # take --contract/--workdir/--arm
KINDS = [None, "trial_finished", "agent_tool", "user_hint", "restored", "epoch_started"]


def _rgb256(rgb) -> int:
    r, g, b = rgb
    return 16 + 36 * round(r / 255 * 5) + 6 * round(g / 255 * 5) + round(b / 255 * 5)


class Data:
    """what the loader thread produced for the current view; replaced as a whole, so the UI thread never sees a half-written one"""
    def __init__(self):
        self.value: dict | None = None
        self.error: str | None = None
        self.at: float = 0.0
        self.key = None


class App:
    def __init__(self, contract: str | None = None, workdir: str | None = None, arm: str | None = None):
        # History and the data catalog do not depend on an active experiment.
        # Resolve the evaluation contract only when opening a live Source.
        self.contract = contract
        self.workdirs = model.discover_workdirs([workdir])
        if workdir and not self.workdirs:
            raise FileNotFoundError(f"no ledger in {workdir}")
        self.wd_i = 0
        self.arm = arm
        self.screen, self.stack = "home", []
        self.ctx = Ctx(version="v" + __import__("antslab.update", fromlist=["x"]).current_version())
        self.filt: dict = {}
        self.data = Data()
        self.sources: dict[str, model.Source] = {}
        self.failed: dict = {}
        self.label = "no ledger"
        self.ranks: dict = {}
        self.refreshing = False
        self.load_s = 0.0
        self.frozen = False
        self.note, self.note_until = "", 0.0
        self.refresh = threading.Event()
        self.stop = threading.Event()
        self.detail_id: int | None = None
        self.pager_text, self.pager_title, self.scroll = "", "", 0
        self.help = False
        self.lock = threading.Lock()

    # ---- data ----------------------------------------------------------
    def source(self, wd: Path, arm: str | None = None) -> model.Source:
        """a Source per (workdir, arm); one that cannot be opened is remembered with its reason (and retried after a minute), so a bad
        ledger is a line on a screen and never an exception in the key loop"""
        key = f"{wd}|{arm or ''}"
        bad = self.failed.get(key)
        if bad and time.time() - bad[0] < 60:
            raise bad[1]
        if key not in self.sources:
            try:
                self.contract = model.find_contract(self.contract)
                self.sources[key] = model.Source(wd, self.contract, arm)
                self.failed.pop(key, None)
            except Exception as e:
                self.failed[key] = (time.time(), e)
                raise
        return self.sources[key]

    def current(self) -> model.Source | None:
        """loader thread only"""
        if not self.workdirs:
            return None
        return self.source(self.workdirs[self.wd_i % len(self.workdirs)], self.arm)

    def fleet_sources(self):
        out, errors = [], []
        for wd in self.workdirs:
            try:
                out.append(self.source(wd))
            except Exception as e:
                errors.append({"workdir": str(wd), "arm": Path(wd).name, "error": f"{type(e).__name__}: {str(e)[:80]}"})
        return out, errors

    def key(self):
        with self.lock:
            return (self.screen, tuple(sorted(self.filt.items())), self.detail_id, self.wd_i, self.arm)

    def load(self, screen: str, filt: dict, detail_id: int | None) -> dict:
        if screen == "afterlife":
            from ..archive_index import read_index
            return {"catalog": read_index()}
        if screen == "data":
            try:
                return {"data": model.data_overview()}
            except Exception as e:
                return {"error": model.human_error(e, "The Data screen")}
        if screen == "home" or screen == "fleet":
            srcs, errors = self.fleet_sources()
            out = {"fleet": model.fleet(srcs) + errors}
            if screen == "fleet" and srcs:
                out["equal"] = model.equal_budget([s for s in srcs if s.c.stages == srcs[0].c.stages])      # only arms with the same stages are comparable
            return out
        src = self.current()
        if src is None:
            raise FileNotFoundError("no ledger found")
        self.label = f"{src.workdir.name}/{src.arm}"
        if screen == "overview":
            return {"snap": src.overview()}
        if screen == "board":
            lb = src.leaderboard(10)
            ranks = {r["trial"]: r["rank"] for rows in lb.values() for r in rows}
            out = {"lb": lb, "prev_ranks": dict(self.ranks)}                  # ranks as of the previous refresh: the ▲ ▼ marks
            self.ranks = ranks
            return out
        if screen == "trials":
            return {"rows": src.trials(filt.get("state"), filt.get("stage")), "now": time.time()}
        if screen == "detail":
            return {"detail": src.detail(detail_id)}
        if screen == "events":
            return {"rows": src.events(200, filt.get("kind")), "per_minute": src.events_per_minute(time.time())}
        if screen == "health":
            return {"rows": src.health(), "extra": src.health_extra()}
        if screen == "system":
            return {"info": model.system_info(src, self.fleet_sources()[0])}
        return {}

    def loader(self):
        while not self.stop.is_set():
            with self.lock:
                screen, filt, did = self.screen, dict(self.filt), self.detail_id
            key = self.key()
            if self.frozen and self.data.key == key and self.data.value is not None:
                self.refresh.wait(REFRESH_S); self.refresh.clear()
                continue
            self.refreshing = True
            t0 = time.monotonic()
            try:
                val, err = self.load("detail" if screen == "detail" else screen, filt, did), None
            except Exception as e:                          # the screen shows the reason; the loop goes on
                val, err = None, f"{type(e).__name__}: {str(e)[:200]}"
            d = Data()
            d.value, d.error, d.at, d.key = val, err, time.time(), key
            self.data = d
            self.refreshing = False
            self.load_s = time.monotonic() - t0
            self.refresh.wait(max(REFRESH_S, 4 * self.load_s))                    # a slow ledger is asked less often: the interface stays responsive
            self.refresh.clear()

    # ---- navigation ----------------------------------------------------
    def goto(self, screen: str, push: bool = True):
        if push and self.screen != screen:
            self.stack.append(self.screen)
        self.screen = screen
        self.ctx.sel = self.ctx.tab = self.scroll = 0
        self.help = False
        self.refresh.set()

    def back(self):
        if self.help:
            self.help = False
        elif self.stack:
            self.screen = self.stack.pop()
            self.ctx.sel = self.scroll = 0
            if self.screen == "afterlife":
                self.ctx.sel = getattr(self, "afterlife_selection", 0)
            self.refresh.set()

    def view(self) -> views.View:
        c = self.ctx
        if self.help:
            return views.help_screen(c)
        if self.screen == "pager":
            return views.pager(self.pager_text, c, self.pager_title)
        d = self.data
        if d.key is not None and d.key != self.key():                  # data for another screen or filter: never hand it to this view
            return views.View([[S(" loading…", "dim")]])
        if d.error:
            return views.View([[S(" cannot show this screen: ", "warn"), S(d.error, "none")]])
        if d.value is None:
            return views.View([[S(" loading…", "dim")]])
        c.filt = self.filt
        c.t = time.time()
        c.ascii_only = "UTF" not in (os.environ.get("LC_ALL") or os.environ.get("LC_CTYPE") or os.environ.get("LANG") or "UTF").upper()
        if isinstance(d.value, dict):
            c.prev_ranks = d.value.get("prev_ranks") or {}
        fn = {"home": views.home, "overview": views.overview, "board": views.board, "trials": views.trials, "detail": views.detail, "fleet": views.fleet_screen,
              "events": views.events, "health": views.health, "data": views.data_screen, "system": views.system_screen,
              "afterlife": views.afterlife_screen}[self.screen]
        try:
            v = fn(d.value, c)
        except Exception as e:                              # a view bug must not take the terminal down
            return views.View([[S(f" view error: {type(e).__name__}: {e}", "bad")]])
        v.lines = views.fit(v.lines, c.width)
        return v

    def run_command(self, text: str):
        from .. import cli
        raw = text.lstrip("/ ")
        if raw.split(None, 1)[:1] == ["hint"]:                      # a hint is free text: quotes and apostrophes stay as typed
            argv = ["hint", raw.split(None, 1)[1] if len(raw.split(None, 1)) > 1 else ""]
        else:
            try:
                argv = shlex.split(raw)
            except ValueError as e:
                self.show_text(f"cannot parse: {e}", text)
                return
        if not argv or argv[0] not in PALETTE_COMMANDS:
            self.show_text(f"unknown or not allowed here: {argv[0] if argv else ''}\nallowed: {', '.join(sorted(PALETTE_COMMANDS))}", text)
            return
        try:
            src = self.current()
        except Exception:
            src = None
        if argv[0] in ("sql", "query"):                        # the rest of the line is one statement; only these options are separate
            flags, words, i = [], [], 1
            while i < len(argv):
                if argv[i] == "--schema":
                    flags.append(argv[i]); i += 1
                elif argv[i] in ("--format", "--limit") and i + 1 < len(argv):
                    flags += argv[i:i + 2]; i += 2
                else:
                    words.append(argv[i]); i += 1
            argv = [argv[0], *flags, *([" ".join(words)] if words else [])]
        if src is not None:
            ctx = ["--workdir", str(src.workdir)] + (["--contract", self.contract, "--arm", src.arm] if argv[0] in NEEDS_CONTEXT else [])
            if argv[0] in ("sql", "query") and "--workdir" not in argv:
                argv += ["--workdir", str(src.workdir)]
            elif argv[0] in NEEDS_CONTEXT:
                argv += [x for x in ctx if x not in argv]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                cli.main(argv)
            except SystemExit as e:
                if e.code not in (0, None):
                    buf.write(f"(exit {e.code})\n")
            except Exception as e:
                buf.write(f"error: {type(e).__name__}: {e}\n")
        self.show_text(buf.getvalue() or "(no output)", text)

    def show_text(self, text: str, title: str):
        self.pager_text, self.pager_title = text, title
        self.goto("pager")

    # ---- keys ------------------------------------------------------------
    def on_key(self, ch, curses_mod) -> bool:
        """returns False to quit"""
        K = curses_mod
        c = self.ctx
        v = self.view()
        if ch in (ord("q"), ord("Q")) and not self.help and self.screen != "pager":
            return False
        if ch == ord("?"):
            self.help = not self.help
        elif ch in (27, ord("b")) or ch == K.KEY_BACKSPACE:
            self.back()
        elif ch == ord("/"):
            return self.prompt(K)
        elif ch in (ord("r"), ord("R")):
            self.refresh.set()
        elif ch == ord(" "):
            self.frozen = not self.frozen
            self.say("refresh frozen (space to continue)" if self.frozen else "refresh continues")
            self.refresh.set()
        elif ch == ord("p") and self.screen not in ("pager", "afterlife"):
            self.toggle_pause()
        elif ch == ord("h") and self.screen not in ("pager", "afterlife"):
            return self.prompt(K, "hint ")
        elif ch == ord("]") or ch == ord("["):
            self.step_search(1 if ch == ord("]") else -1)
        elif ch == ord("g"):
            c.sel = 0; self.scroll = 0
        elif ch == ord("G"):
            c.sel = max(v.rows - 1, 0); self.scroll = max(0, len(v.lines) - (c.height - 3))
        elif ord("0") <= ch <= ord("9"):
            order = [name for name, _ in views.SCREENS]
            self.goto(order[ch - ord("0")])
        elif self.screen == "pager" and ch in (K.KEY_DOWN, K.KEY_UP, K.KEY_NPAGE, K.KEY_PPAGE, ord("j"), ord("k")):
            step = {K.KEY_DOWN: 1, ord("j"): 1, K.KEY_UP: -1, ord("k"): -1, K.KEY_NPAGE: 10, K.KEY_PPAGE: -10}[ch]
            self.scroll = max(0, min(self.scroll + step, max(0, len(v.lines) - (c.height - 3))))
        elif ch == K.KEY_DOWN or ch == ord("j"):
            c.sel = min(max(v.rows - 1, 0), c.sel + 1)
        elif ch == K.KEY_UP or ch == ord("k") and self.screen != "events":
            c.sel = max(0, c.sel - 1)
        elif ch == K.KEY_NPAGE:
            c.sel = min(max(v.rows - 1, 0), c.sel + 10)
        elif ch == K.KEY_PPAGE:
            c.sel = max(0, c.sel - 10)
        elif self.screen == "board" and ch in (9, ord("."), ord(">"), ord("c")):        # the cap tabs have their own keys: ←/→ always move between screens
            c.tab += 1; c.sel = 0
        elif self.screen == "board" and ch in (getattr(K, "KEY_BTAB", -2), ord(","), ord("<"), ord("C")):
            c.tab -= 1; c.sel = 0
        elif ch == K.KEY_RIGHT:
            self.cycle_screen(1)
        elif ch == K.KEY_LEFT:
            self.cycle_screen(-1)
        elif ch in (10, 13, K.KEY_ENTER):
            self.enter(v)
        elif ch == ord("s") and self.screen == "trials":
            i = views.STATES.index(self.filt.get("state"))
            self.filt["state"] = views.STATES[(i + 1) % len(views.STATES)]; c.sel = 0; self.refresh.set()
        elif ch == ord("t") and self.screen == "trials":
            order = [None, "s0", "s1", "s2"]
            self.filt["stage"] = order[(order.index(self.filt.get("stage")) + 1) % len(order)] if self.filt.get("stage") in order else None; c.sel = 0; self.refresh.set()
        elif ch == ord("k") and self.screen == "events":
            self.filt["kind"] = KINDS[(KINDS.index(self.filt.get("kind")) + 1) % len(KINDS)] if self.filt.get("kind") in KINDS else None; self.refresh.set()
        elif ch == ord("w") and len(self.workdirs) > 1:
            self.wd_i = (self.wd_i + 1) % len(self.workdirs); self.arm = None; self.refresh.set()
        elif ch == ord("a"):
            self.next_arm()
        return True

    def say(self, text: str, seconds: float = 6.0):
        self.note, self.note_until = text, time.time() + seconds

    def toggle_pause(self):
        """the PAUSE flag of the search on screen, through the ordinary CLI command (the only thing here that writes, besides a hint)"""
        from .. import cli, ui
        try:
            src = self.current()
        except Exception as e:
            self.say(f"cannot pause: {e}")
            return
        if src is None:
            return
        flag = Path(src.workdir) / ui.PAUSE_FILE
        cmd = "resume" if flag.exists() else "pause"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                cli.main([cmd, "--workdir", str(src.workdir), "--contract", self.contract, "--arm", src.arm])
            except SystemExit:
                pass
            except Exception as e:
                buf.write(f"error: {e}")
        self.say(f"{src.workdir.name}/{src.arm}: " + (buf.getvalue().strip().splitlines() or [cmd])[0][:80])
        self.refresh.set()

    def step_search(self, d: int):
        if not self.workdirs:
            return
        self.wd_i = (self.wd_i + d) % len(self.workdirs)
        self.arm = None
        self.ctx.sel = 0
        self.refresh.set()
        self.say(f"now looking at {self.workdirs[self.wd_i].name}")

    def cycle_screen(self, d: int):
        order = [name for name, _ in views.SCREENS]
        i = order.index(self.screen) if self.screen in order else 0
        self.goto(order[(i + d) % len(order)], push=False)

    def next_arm(self):
        try:
            src = self.current()
        except Exception:
            return
        if src is None:
            return
        arms = [r[0] for r in src.led.conn.execute("SELECT DISTINCT arm FROM trials ORDER BY arm")]
        if len(arms) > 1:
            self.arm = arms[(arms.index(src.arm) + 1) % len(arms)] if src.arm in arms else arms[0]
            self.refresh.set()

    def enter(self, v: views.View):
        c = self.ctx
        d = self.data.value or {}
        if self.screen == "afterlife":
            import json
            from ..archive_index import read_round
            catalog = d.get("catalog", {})
            rows = catalog.get("rounds", [])
            if c.sel < len(rows):
                self.afterlife_selection = c.sel
                try:
                    self.pager_text = views.afterlife_detail(read_round(catalog, rows[c.sel]), c.width)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    self.pager_text = f"Cannot read archived summary: {exc}\n\nReturn with Esc. Check the cached file and its permissions, or rebuild\nthe cache from the verified bundle. No running experiment was changed."
                self.pager_title = "Afterlife · sealed round detail (read-only)"
                self.goto("pager")
        elif self.screen == "board":
            lb = d.get("lb", {})
            caps = list(lb)
            if caps:
                rows = lb[caps[c.tab % len(caps)]]
                if c.sel < len(rows):
                    self.detail_id = int(rows[c.sel]["trial"]); self.goto("detail")
        elif self.screen == "trials":
            rows = d.get("rows", [])
            if c.sel < len(rows):
                self.detail_id = int(rows[c.sel]["id"]); self.goto("detail")
        elif self.screen == "fleet":
            rows = d.get("fleet", [])
            if c.sel < len(rows) and "error" not in rows[c.sel]:
                wd = rows[c.sel]["workdir"]
                for i, p in enumerate(self.workdirs):
                    if str(p) == wd:
                        self.wd_i, self.arm = i, rows[c.sel].get("arm_name") or rows[c.sel]["arm"]
                self.goto("overview")

    def prompt(self, K, initial: str = "") -> bool:
        scr = self.scr
        buf = initial
        scr.timeout(-1)
        K.curs_set(1)
        try:
            while True:
                try:
                    h, w = scr.getmaxyx()                              # re-read every key: the window may have been resized
                    scr.move(h - 1, 0); scr.clrtoeol()
                    scr.addnstr(h - 1, 0, ("/" + buf)[-(w - 2):] if w > 2 else "", max(w - 1, 0))
                    ch = scr.get_wch()
                except K.error:
                    continue
                except KeyboardInterrupt:
                    return True
                if ch == K.KEY_RESIZE:
                    continue
                if ch in ("\n", "\r"):
                    break
                if ch == "\x1b":
                    return True
                if ch in ("\x7f", "\b", K.KEY_BACKSPACE):
                    buf = buf[:-1]
                elif isinstance(ch, str) and ch.isprintable():
                    buf += ch
        finally:
            K.curs_set(0)
            scr.timeout(400 if anim.slow_link() else 150)
        if buf.strip():
            self.run_command(buf)
        return True

    # ---- drawing ---------------------------------------------------------
    def draw(self, scr, K, paint):
        h, w = scr.getmaxyx()
        self.ctx.width, self.ctx.height = w, h
        v = self.view()
        scr.erase()
        body_h = h - 2
        c = self.ctx
        top = self.scroll if self.screen == "pager" else 0
        if v.rows and self.screen != "pager":
            r = v.row0 + c.sel
            top = max(0, r - body_h + 3) if r >= body_h - 2 else 0
        for y, line in enumerate(v.lines[top: top + body_h]):
            x = 0
            for text, style in line:
                if x >= w - 1:
                    break
                try:
                    scr.addnstr(y, x, text, w - 1 - x, paint(style))
                except K.error:
                    pass
                x += len(text)
        tabs = " ".join((f"[{k}]" if self.screen == s or (self.screen == "detail" and s == "trials") else k) for s, k in
                        [(s, str(i)) for i, (s, _) in enumerate(views.SCREENS)])
        now = time.time()
        glyph = anim.frame("loading" if self.refreshing else "alive", now, c.ascii_only)
        status = f" {glyph} {self.label} · {tabs}"
        hint = views.KEYS.get(self.screen, views.KEYS["home"])
        try:
            if self.note and now < self.note_until:
                scr.addnstr(h - 2, 0, " " + self.note[: w - 3], w - 1, paint("accent"))
            else:
                scr.addnstr(h - 2, 0, " " + hint[: w - 3], w - 1, paint("dim"))
            scr.addnstr(h - 1, 0, status[: w - 1], w - 1, paint("dim"))
            age = now - self.data.at if self.data.at else None
            if age is not None:
                tag = ("frozen " if self.frozen else "") + (f"slow ledger ({self.load_s:.1f}s) " if self.load_s > 0.8 else "") + f"{age:.0f}s ago "
                scr.addnstr(h - 1, max(0, w - len(tag) - 1), tag, len(tag), paint("warn" if self.frozen else "dim"))
        except K.error:
            pass
        scr.refresh()


def make_painter(K):
    pairs: dict = {}
    mono = K.COLORS < 256 if hasattr(K, "COLORS") else True

    def idx(name):
        return None if name is None else _rgb256(render.RGB[name])

    def pair(fg, bg):
        key = (fg, bg)
        if key not in pairs:
            pairs[key] = len(pairs) + 1
            K.init_pair(pairs[key], -1 if fg is None else fg, -1 if bg is None else bg)
        return K.color_pair(pairs[key])

    def paint(style):
        if mono or "NO_COLOR" in os.environ:
            if isinstance(style, tuple):
                return K.A_REVERSE if style[1] and not style[0] else 0
            fg, bold, dim = render.NAMED.get(style, render.NAMED["none"])
            return (K.A_BOLD if bold else 0) | (K.A_DIM if dim else 0)
        if isinstance(style, tuple):
            return pair(idx(style[0]), idx(style[1]))
        fg, bold, dim = render.NAMED.get(style, render.NAMED["none"])
        return pair(idx(fg), None) | (K.A_BOLD if bold else 0) | (K.A_DIM if dim else 0)
    return paint


def run(contract=None, workdir=None, arm=None) -> int:
    import curses
    import locale
    locale.setlocale(locale.LC_ALL, "")
    app = App(contract, workdir, arm)

    def main(scr):
        app.scr = scr
        curses.curs_set(0)
        curses.use_default_colors() if curses.has_colors() else None
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
        scr.keypad(True)
        scr.timeout(400 if anim.slow_link() else 150)             # the key loop is the animation clock: about 7 frames a second, 2.5 over ssh
        paint = make_painter(curses)
        t = threading.Thread(target=app.loader, daemon=True)
        t.start()
        last = (0, 0, None)
        try:
            while True:
                try:
                    ch = scr.getch()
                except KeyboardInterrupt:
                    break
                if ch == curses.KEY_RESIZE:
                    scr.clear()
                elif ch != -1:
                    if not app.on_key(ch, curses):
                        break
                app.draw(scr, curses, paint)
        finally:
            app.stop.set(); app.refresh.set()
    try:
        curses.wrapper(main)
    except curses.error as e:
        print(f"ants: the terminal could not start the interface ({e}); use `ants shell`", file=sys.stderr)
        return 1
    return 0


def once(contract=None, workdir=None, arm=None, width: int = 100, screen: str = "home", color: bool | None = None, stream=None) -> str:
    """the same screen without a terminal: for pipes, `ants home --once`, tests and screenshots"""
    app = App(contract, workdir, arm)
    app.ctx.width, app.ctx.height = width, 40
    d = app.load(screen, {}, None) if screen not in ("pager",) else {}
    app.screen = screen
    d_obj = Data(); d_obj.value = d; d_obj.at = time.time()
    app.data = d_obj
    v = app.view()
    lines = v.lines
    stream = stream or sys.stdout
    use = render.color_enabled(stream) if color is None else color
    return render.ansi(lines) if use else render.plain(lines)

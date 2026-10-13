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

from . import model, render, views
from .views import Ctx, S

REFRESH_S = 3.0
PALETTE_COMMANDS = {"best", "show", "diff", "log", "doctor", "status", "top", "sql", "query", "backups", "hint", "pause", "resume", "export", "workspace"}
NEEDS_CONTEXT = {"best", "show", "diff", "log", "doctor", "status", "top", "hint", "pause", "resume", "export", "workspace"}   # take --contract/--workdir/--arm
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
        self.contract = model.find_contract(contract)
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
        self.refresh = threading.Event()
        self.stop = threading.Event()
        self.detail_id: int | None = None
        self.pager_text, self.pager_title, self.scroll = "", "", 0
        self.help = False
        self.lock = threading.Lock()

    # ---- data ----------------------------------------------------------
    def source(self, wd: Path, arm: str | None = None) -> model.Source:
        key = f"{wd}|{arm or ''}"
        if key not in self.sources:
            self.sources[key] = model.Source(wd, self.contract, arm)
        return self.sources[key]

    def current(self) -> model.Source | None:
        if not self.workdirs:
            return None
        return self.source(self.workdirs[self.wd_i % len(self.workdirs)], self.arm)

    def fleet_sources(self) -> list[model.Source]:
        out = []
        for wd in self.workdirs:
            try:
                out.append(self.source(wd))
            except Exception:
                continue
        return out

    def load(self, screen: str, filt: dict, detail_id: int | None) -> dict:
        src = self.current()
        if screen == "home" or screen == "fleet":
            srcs = self.fleet_sources()
            out = {"fleet": model.fleet(srcs)}
            if screen == "fleet":
                out["equal"] = model.equal_budget([s for s in srcs if s.c.stages == srcs[0].c.stages])      # only arms with the same stages are comparable
            return out
        if src is None:
            raise FileNotFoundError("no ledger found")
        if screen == "overview":
            return {"snap": src.overview()}
        if screen == "board":
            return {"lb": src.leaderboard(10)}
        if screen == "trials":
            return {"rows": src.trials(filt.get("state"), filt.get("stage")), "now": time.time()}
        if screen == "detail":
            return {"detail": src.detail(detail_id)}
        if screen == "events":
            return {"rows": src.events(200, filt.get("kind"))}
        if screen == "health":
            return {"rows": src.health()}
        if screen == "data":
            try:
                return {"data": model.data_overview()}
            except Exception as e:
                return {"error": f"{type(e).__name__}: {e}"}
        if screen == "system":
            return {"info": model.system_info(src)}
        return {}

    def loader(self):
        while not self.stop.is_set():
            with self.lock:
                screen, filt, did = self.screen, dict(self.filt), self.detail_id
                key = (screen, tuple(sorted(filt.items())), did, self.wd_i, self.arm)
            try:
                val, err = self.load("detail" if screen == "detail" else screen, filt, did), None
            except Exception as e:                          # the screen shows the reason; the loop goes on
                val, err = None, f"{type(e).__name__}: {str(e)[:200]}"
            d = Data()
            d.value, d.error, d.at, d.key = val, err, time.time(), key
            self.data = d
            self.refresh.wait(REFRESH_S)
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
            self.refresh.set()

    def view(self) -> views.View:
        c = self.ctx
        if self.help:
            return views.help_screen(c)
        if self.screen == "pager":
            return views.pager(self.pager_text, c, self.pager_title)
        d = self.data
        if d.error:
            return views.View([[S(" cannot show this screen: ", "warn"), S(d.error, "none")]])
        if d.value is None:
            return views.View([[S(" loading…", "dim")]])
        c.filt = self.filt
        fn = {"home": views.home, "overview": views.overview, "board": views.board, "trials": views.trials, "detail": views.detail, "fleet": views.fleet_screen,
              "events": views.events, "health": views.health, "data": views.data_screen, "system": views.system_screen}[self.screen]
        try:
            v = fn(d.value, c)
        except Exception as e:                              # a view bug must not take the terminal down
            return views.View([[S(f" view error: {type(e).__name__}: {e}", "bad")]])
        v.lines = views.fit(v.lines, c.width)
        return v

    def run_command(self, text: str):
        from .. import cli
        try:
            argv = shlex.split(text.lstrip("/ "))
        except ValueError as e:
            self.show_text(f"cannot parse: {e}", text)
            return
        if not argv or argv[0] not in PALETTE_COMMANDS:
            self.show_text(f"unknown or not allowed here: {argv[0] if argv else ''}\nallowed: {', '.join(sorted(PALETTE_COMMANDS))}", text)
            return
        src = self.current()
        if argv[0] == "hint":
            argv = ["hint", " ".join(argv[1:])]
        elif argv[0] in ("sql", "query"):                        # the rest of the line is one statement; only these options are separate
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
        elif ord("0") <= ch <= ord("8"):
            order = ["home", "overview", "board", "trials", "fleet", "events", "health", "data", "system"]
            self.goto(order[ch - ord("0")])
        elif ch == K.KEY_DOWN or ch == ord("j"):
            c.sel = min(max(v.rows - 1, 0), c.sel + 1)
        elif ch == K.KEY_UP or ch == ord("k") and self.screen != "events":
            c.sel = max(0, c.sel - 1)
        elif ch == K.KEY_NPAGE:
            c.sel = min(max(v.rows - 1, 0), c.sel + 10); self.scroll += 10
        elif ch == K.KEY_PPAGE:
            c.sel = max(0, c.sel - 10); self.scroll = max(0, self.scroll - 10)
        elif ch == K.KEY_RIGHT:
            if self.screen == "board":
                c.tab += 1; c.sel = 0
            else:
                self.cycle_screen(1)
        elif ch == K.KEY_LEFT:
            if self.screen == "board":
                c.tab -= 1; c.sel = 0
            else:
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

    def cycle_screen(self, d: int):
        order = ["home", "overview", "board", "trials", "fleet", "events", "health", "data", "system"]
        i = order.index(self.screen) if self.screen in order else 0
        self.goto(order[(i + d) % len(order)], push=False)

    def next_arm(self):
        src = self.current()
        if src is None:
            return
        arms = [r[0] for r in src.led.conn.execute("SELECT DISTINCT arm FROM trials ORDER BY arm")]
        if len(arms) > 1:
            self.arm = arms[(arms.index(src.arm) + 1) % len(arms)] if src.arm in arms else arms[0]
            self.refresh.set()

    def enter(self, v: views.View):
        c = self.ctx
        d = self.data.value or {}
        if self.screen == "board":
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

    def prompt(self, K) -> bool:
        scr = self.scr
        h, w = scr.getmaxyx()
        buf = ""
        scr.timeout(-1)
        K.curs_set(1)
        try:
            while True:
                scr.move(h - 1, 0); scr.clrtoeol()
                scr.addnstr(h - 1, 0, "/" + buf, w - 1)
                ch = scr.get_wch()
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
            scr.timeout(200)
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
        src = self.current()
        where = f"{src.workdir.name}/{src.arm}" if src else "no ledger"
        status = f" {where} · {tabs} · / command · ? help · q quit"
        try:
            scr.addnstr(h - 1, 0, status[: w - 1], w - 1, paint("dim"))
            age = time.time() - self.data.at if self.data.at else None
            if age is not None:
                tag = f"{age:.0f}s ago "
                scr.addnstr(h - 1, max(0, w - len(tag) - 1), tag, len(tag), paint("dim"))
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
        scr.timeout(200)
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

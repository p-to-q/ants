"""Projection surface: render the ledger as board.json (machines) + board.html (people). Read-only; never a dependency of the loop.

The 'beauty contest': per size cap, the ranked models with lineage, plus category belts (smallest within 5% of the best,
most robust, best rhythm, fastest-rising research path). Self-contained HTML, no external requests.
"""
from __future__ import annotations

import html
import http.server
import json
import os
import stat
import tempfile
import time
import urllib.parse
from pathlib import Path

from .history import History
from .landscape import diagnostics
from .lineage import ancestry
from .textsafe import clean_text


def _spark(vals, w=180, h=36) -> str:
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    pts = " ".join(f"{i * w / (len(vals) - 1):.1f},{h - 3 - (v - lo) / span * (h - 6):.1f}" for i, v in enumerate(vals))
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}"><polyline fill="none" stroke="currentColor" stroke-width="1.5" points="{pts}"/></svg>'


def _diff(c, spec) -> str:
    d = c.space.defaults()
    return clean_text(", ".join(f"{k}={spec[k]:.4g}" if isinstance(spec[k], float) else f"{k}={spec[k]}"
                                for k in c.space.active_names(spec) if spec[k] != d.get(k)), 140) or "(defaults)"


def build(ledger, c, arms: list[str], out_dir, top: int = 5) -> dict:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    data = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "task": c.name, "primary": c.primary, "direction": c.direction,
            "chain_ok": ledger.verify_chain()[0], "chain_head": ledger.head()[:16], "arms": {}}
    for arm in arms:
        h = History(ledger, c, arm); h.update()
        diag = diagnostics(h)
        feas = [r for r in h.first.values() if r.get("feasible", True)]
        bins: dict = {}
        for r in sorted(feas, key=lambda r: h.sign * r["value"]):
            bins.setdefault(h.cap_bin(r["metrics"]), []).append(r)
        board = {}
        niche_lab = {tuple(n["niche"]): n["label"] for n in diag["niches"]}
        for b, rs in sorted(bins.items(), key=lambda kv: kv[0]):
            if b == float("inf"):
                continue
            rows = []
            for rank, r in enumerate(rs[:top], 1):
                rows.append({"rank": rank, "value": round(r["value"], 5), "n_params": r["metrics"].get(c.cap_metric),
                             "bytes": r["metrics"].get("bytes"), "operator": r["source"].split(":")[0],
                             "path": "/".join(map(str, c.space.niche(r["spec"]))), "path_label": niche_lab.get(c.space.niche(r["spec"]), "?"),
                             "lineage_depth": len(ancestry(h, r["key"])) - 1, "spec": _diff(c, r["spec"]), "trial": r["id"]})
            board[str(b)] = rows
        best = min((r for r in feas), key=lambda r: h.sign * r["value"], default=None)
        belts = {}
        if best:
            near = [r for r in feas if h.sign * (r["value"] - best["value"]) <= 0.05 * abs(best["value"])]
            sm = min(near, key=lambda r: r["metrics"].get(c.cap_metric, 1e18))
            belts["smallest within 5% of best"] = {"trial": sm["id"], "n_params": sm["metrics"].get(c.cap_metric), "value": round(sm["value"], 5)}
            deep = [r for r in feas if "robustness_index" in r["metrics"]]
            if deep:
                rb = min(deep, key=lambda r: r["metrics"]["robustness_index"])
                belts["most robust"] = {"trial": rb["id"], "robustness_index": round(rb["metrics"]["robustness_index"], 3)}
                sf = min((r for r in deep if "seasonal_fidelity" in r["metrics"]), key=lambda r: r["metrics"]["seasonal_fidelity"], default=None)
                if sf:
                    belts["best rhythm"] = {"trial": sf["id"], "seasonal_fidelity": round(sf["metrics"]["seasonal_fidelity"], 3)}
        risers = sorted((n for n in diag["niches"] if n["label"] == "riser"), key=lambda n: -n["velocity"])
        if risers:
            belts["fastest-rising path"] = {"path": "/".join(map(str, risers[0]["niche"])), "velocity": round(risers[0]["velocity"], 5)}
        curve, best_so_far = [], None
        for r in h.order:
            if r.get("feasible", True) and (best_so_far is None or h.sign * r["value"] < h.sign * best_so_far):
                best_so_far = r["value"]
            curve.append(best_so_far if best_so_far is not None else r["value"])
        step = max(1, len(curve) // 120)
        data["arms"][arm] = {"evaluations": diag["evaluations"], "board": board, "belts": belts, "curve": curve[::step],
                             "operators": diag["operators"], "niches": [{**n, "niche": "/".join(map(str, n["niche"]))} for n in diag["niches"][:12]],
                             "ruggedness": diag["ruggedness"], "stagnant_for": diag["stagnant_for"],
                             "stepping": diag["stepping_stones"]}
    calls = ledger.conn.execute("SELECT judge, kind, COUNT(*), COALESCE(SUM(outcome IS NOT NULL),0) FROM judgements GROUP BY judge, kind").fetchall()
    data["judges"] = [{"judge": a, "kind": b, "predictions": n, "resolved": r} for a, b, n, r in calls]
    _write_atomic(out / "board.json", json.dumps(data, indent=1, default=str))
    _write_atomic(out / "board.html", render(data))
    return data


def _write_atomic(path: Path, text: str) -> None:
    """The board is served while it is rewritten: write a sibling and rename, so a reader never sees half a page."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".board_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


CSS = """:root{--bg:#fafaf7;--fg:#1d1d1b;--mut:#6b6b66;--line:#dcdcd4;--acc:#1f6f5c;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecece6;--mut:#9a9a92;--line:#33332f;--acc:#6fcab0;--card:#1e1e1b}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}main{max-width:1100px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:20px;margin:0 0 2px}h2{font-size:15px;margin:26px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.mut{color:var(--mut)}table{border-collapse:collapse;width:100%;background:var(--card)}th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}
th{font-weight:600;color:var(--mut);font-size:12px}.num{text-align:right;font-variant-numeric:tabular-nums}code{font-size:12px}
.belt{display:inline-block;border:1px solid var(--line);border-radius:6px;padding:4px 8px;margin:0 6px 6px 0;background:var(--card)}
.tag{font-size:11px;border-radius:4px;padding:1px 5px;border:1px solid var(--line)}.leader{color:var(--acc);border-color:var(--acc)}.riser{color:#b8650a;border-color:#b8650a}
svg{color:var(--acc);vertical-align:middle}"""


def render(d: dict) -> str:
    """Every value is passed through html.escape, whatever its type: the data comes from the ledger and from gym output, not from us.
    The page also carries a Content-Security-Policy that forbids scripts and any network request, in case an escape is ever missed."""
    def e(x):
        return html.escape(x if isinstance(x, str) else str(x))

    def n(x, f=""):
        return e(format(x, f)) if isinstance(x, (int, float)) and not isinstance(x, bool) else e(x)
    parts = [f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<meta http-equiv=Content-Security-Policy content=\"{html.escape(META_CSP)}\">"
             f"<meta http-equiv=refresh content=60><title>ants board</title><style>{CSS}</style><main>"
             f"<h1>{e(d['task'])}</h1><div class=mut>updated {e(d['generated'])} · headline <code>{e(d['primary'])}</code> "
             f"({e(d['direction'])} is better) · ledger chain {'intact' if d['chain_ok'] else '<b>BROKEN</b>'} <code>{e(d['chain_head'])}</code></div>"]
    for arm, a in d["arms"].items():
        parts.append(f"<h2>{e(arm)} · {n(a['evaluations'])} evaluations {_spark([v for v in a['curve'] if isinstance(v, (int, float))])}</h2>")
        if a["belts"]:
            parts.append("".join(f"<span class=belt><b>{e(k)}</b> {e(json.dumps(v))}</span>" for k, v in a["belts"].items()))
        for cap, rows in a["board"].items():
            parts.append(f"<h3 class=mut style='font-size:13px;margin:14px 0 4px'>cap ≤ {e(cap)} parameters</h3><table><tr><th>#<th class=num>{e(d['primary'])}"
                         "<th class=num>params<th>operator<th>path<th class=num>depth<th>spec (non-default levers)")
            for r in rows:
                parts.append(f"<tr><td>{n(r['rank'])}<td class=num>{n(r['value'])}<td class=num>{n(r['n_params'])}<td>{e(r['operator'])}"
                             f"<td>{e(r['path'])} <span class='tag {e(r['path_label'])}'>{e(r['path_label'])}</span><td class=num>{n(r['lineage_depth'])}<td><code>{e(r['spec'])}</code>")
            parts.append("</table>")
        parts.append("<h3 class=mut style='font-size:13px;margin:14px 0 4px'>research paths: level, velocity, acceleration</h3><table><tr><th>path<th class=num>n<th class=num>best<th class=num>velocity<th class=num>acceleration<th>label")
        for q in a["niches"]:
            parts.append(f"<tr><td><code>{e(q['niche'])}</code><td class=num>{n(q['n'])}<td class=num>{n(q['best'], '.4f')}<td class=num>{n(q['velocity'], '.5f')}<td class=num>{n(q['acceleration'], '.5f')}<td><span class='tag {e(q['label'])}'>{e(q['label'])}</span>")
        parts.append("</table>")
        parts.append("<h3 class=mut style='font-size:13px;margin:14px 0 4px'>search mechanisms: discovery rate</h3><table><tr><th>operator<th class=num>proposals<th class=num>improved archive<th class=num>rate")
        for k, v in sorted(a["operators"].items(), key=lambda kv: -kv[1]["succ"] / max(1, kv[1]["n"])):
            parts.append(f"<tr><td>{e(k)}<td class=num>{n(v['n'])}<td class=num>{n(v['succ'])}<td class=num>{v['succ'] / max(1, v['n']):.2f}")
        s = a["stepping"]
        parts.append(f"</table><p class=mut>ruggedness {n(a['ruggedness'])} · stagnant for {n(a['stagnant_for'])} evals · champion ancestors that were not leaders: "
                     f"{n(s.get('non_leader_ancestor_frac'))} · lead changed: {n(s.get('lead_changed'))}</p>")
    if d["judges"]:
        parts.append("<h2>judges</h2><table><tr><th>judge<th>decision<th class=num>predictions<th class=num>resolved")
        parts += [f"<tr><td>{e(j['judge'])}<td>{e(j['kind'])}<td class=num>{n(j['predictions'])}<td class=num>{n(j['resolved'])}" for j in d["judges"]]
        parts.append("</table>")
    return "".join(parts) + "</main>"


# --------------------------------------------------------------------------------------------------- serving
CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
META_CSP = CSP.replace("; frame-ancestors 'none'", "")             # frame-ancestors is only honoured as an HTTP header
LOOPBACK = ("127.0.0.1", "::1", "localhost")
SERVED = {"/": ("board.html", "text/html; charset=utf-8"), "/board.html": ("board.html", "text/html; charset=utf-8"),
          "/board.json": ("board.json", "application/json")}
MAX_SERVED_BYTES = 32 << 20


class BoardHandler(http.server.BaseHTTPRequestHandler):
    """Serves exactly board.html and board.json from one directory. No listing, no other files, no symlinks, GET/HEAD only, a Host
    check against DNS rebinding (a web page in the operator's browser must not be able to read the board), strict security headers."""
    server_version, sys_version, protocol_version = "ants-board", "", "HTTP/1.0"
    timeout = 10                                                # a stalled client cannot hold a thread forever
    directory = "."

    def log_message(self, *args):                               # request lines are attacker-controlled: do not copy them to the journal
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        name = host[1:host.index("]")] if host.startswith("[") and "]" in host else host.rsplit(":", 1)[0] if ":" in host else host
        port = host.rsplit(":", 1)[1] if host.rsplit("]", 1)[-1].count(":") else ""
        return name in LOOPBACK and port in ("", str(self.server.server_port))

    def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8", head: bool = False):
        self.send_response(code)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(body))), ("Content-Security-Policy", CSP),
                     ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"), ("Cache-Control", "no-store"),
                     ("Cross-Origin-Resource-Policy", "same-origin"), ("X-Frame-Options", "DENY")):
            self.send_header(k, v)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _serve(self, head: bool):
        if not self._host_ok():
            return self._send(403, b"forbidden host\n", head=head)
        entry = SERVED.get(urllib.parse.urlsplit(self.path).path)
        if entry is None:
            return self._send(404, b"not found\n", head=head)
        try:
            fd = os.open(os.path.join(self.directory, entry[0]), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_SERVED_BYTES:
                    return self._send(404, b"not found\n", head=head)
                with os.fdopen(fd, "rb", closefd=False) as f:
                    body = f.read(MAX_SERVED_BYTES)
            finally:
                os.close(fd)
        except OSError:
            return self._send(404, b"not found\n", head=head)
        self._send(200, body, entry[1], head)

    def do_GET(self):
        self._serve(False)

    def do_HEAD(self):
        self._serve(True)

    def _refuse(self):
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()
    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse


def make_server(directory, port: int, host: str = "127.0.0.1") -> http.server.ThreadingHTTPServer:
    """The board server. It refuses to bind anything but a loopback address: reach it through an SSH tunnel."""
    if host not in LOOPBACK or host == "localhost":
        raise ValueError(f"the board binds to loopback only (127.0.0.1 or ::1), not {host!r}")
    handler = type("Handler", (BoardHandler,), {"directory": str(directory)})
    srv = http.server.ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv

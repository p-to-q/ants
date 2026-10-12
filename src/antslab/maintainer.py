"""Maintainer: lets an LLM repair or tweak gym code under a strict gate, with rollback.

Principle (cognition-authority separation): the LLM only *drafts* a patch. Deterministic code decides whether it may touch the
tree: path allow/deny lists, size limits, forbidden constructs, a rate limit, a sandbox copy, the test-suite, GOLDEN REPLAY
(a repair must not change a single metric that was already correct) and a repair check (the failing spec must now succeed).
Every step is a ledger event; every applied patch can be rolled back byte-for-byte.
Never patchable here: ledger, gate, runner, contract, campaign, registry, metrics, judge, tests, contracts. The gym files themselves are patchable (static analysis only; this is not a sandbox).
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Edit:
    path: str
    old: str
    new: str


@dataclass
class Patch:
    edits: list
    rationale: str = ""
    model: str = ""

    def digest(self) -> str:
        return hashlib.sha256(json.dumps([(e.path, e.old, e.new) for e in self.edits]).encode()).hexdigest()[:16]


# ------------------------------------------------------------------------------------------------ static (AST) gate
# A deny-list of raw tokens is trivially bypassed (`getattr(__builtins__, 'op'+'en')`, `open (`, `from os import system`, ...).
# The gate therefore parses the file before and after the patch and rejects the patch if it adds ANY dangerous construct:
#   * an import outside SAFE_IMPORTS, a call/reference to a dynamic-execution or I/O builtin, an I/O / process / env attribute,
#     dunder access, or a format string that walks attributes;
#   * a change to any function (or module-level statement) that already contains such a construct, to a name those use, or to a
#     call-site of such a function ("freeze what performs I/O"), so existing dangerous code can neither be re-aimed nor newly reached.
# This is best-effort static analysis, NOT a sandbox: it narrows what a hostile patch can express; the other gates (tests, golden
# replay, rate limit, clean environment, rlimits) stay in place. Anything the parser cannot handle fails closed.
SAFE_IMPORTS = frozenset({"numpy", "math", "json", "time", "dataclasses", "typing", "functools", "itertools", "collections",
                          "argparse", "random", "statistics", "bisect", "heapq", "pathlib", "__future__"})
SAFE_RELATIVE = frozenset({"data", "metrics", "synth_suite"})             # `from ..data import Catalog`; never ledger / runner / gate / llm / ...
DANGEROUS_NAMES = frozenset({"eval", "exec", "compile", "open", "__import__", "getattr", "setattr", "delattr", "globals", "locals",
                             "vars", "input", "breakpoint"})
DANGEROUS_ATTRS = frozenset({
    "read_text", "write_text", "read_bytes", "write_bytes", "open", "unlink", "rmtree", "rmdir", "remove", "removedirs", "rename",
    "renames", "chmod", "chown", "symlink_to", "hardlink_to", "link", "symlink", "mkdir", "makedirs", "touch", "truncate",
    "system", "popen", "Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput", "fork", "forkpty",
    "kill", "killpg", "startfile", "putenv", "unsetenv", "getenv", "environ", "environb", "argv", "fdopen", "dup", "dup2", "pipe",
    "connect", "connect_ex", "bind", "listen", "accept", "send", "sendall", "sendto", "recv", "create_connection", "urlopen",
    "urlretrieve", "gethostbyname", "getaddrinfo", "import_module", "find_spec", "load_module", "exec_module", "reload",
    "loadtxt", "genfromtxt", "fromfile", "tofile", "memmap", "savetxt", "save", "savez", "savez_compressed",
    "f2py", "ctypeslib", "distutils", "modules", "meta_path", "path_hooks", "settrace", "setprofile", "gettrace", "_getframe",
    "currentframe", "f_globals", "f_locals", "f_builtins", "f_back", "tb_frame", "gi_frame", "cr_frame", "gi_code", "co_code",
    "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe"})
ALLOWED_DUNDERS = frozenset({"__init__", "__name__", "__main__", "__doc__", "__all__", "__slots__", "__post_init__", "__repr__",
                             "__str__", "__len__", "__iter__", "__next__", "__eq__", "__ne__", "__lt__", "__le__", "__gt__",
                             "__ge__", "__hash__", "__contains__", "__getitem__", "__setitem__", "__enter__", "__exit__",
                             "__call__", "__bool__", "__float__", "__int__", "__add__", "__mul__", "__annotations__"})
_DUNDER = re.compile(r"__\w+__")
_FMT_WALK = re.compile(r"\{[^{}:!]*\.[A-Za-z_]")


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__") and name not in ALLOWED_DUNDERS


def _callee(call: ast.Call):
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None


def _is_danger_attr(a: str) -> bool:
    return a in DANGEROUS_ATTRS or a.startswith("spawn")


def danger_profile(src: str, safe_imports=SAFE_IMPORTS) -> Counter:
    """Multiset of dangerous constructs (and frozen units) in `src`. Raises SyntaxError / ValueError / RecursionError / MemoryError."""
    tree = ast.parse(src)
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    found = []                                                   # (node, key)
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            found += [(n, ("import", a.name)) for a in n.names if a.name.split(".")[0] not in safe_imports]
        elif isinstance(n, ast.ImportFrom):
            mod = "." * n.level + (n.module or "")
            if n.level >= 2:                                     # project modules: only read-only data helpers may be imported
                targets = [(n.module or "").split(".")[0]] if n.module else [a.name for a in n.names]
                if any(t not in SAFE_RELATIVE for t in targets):
                    found.append((n, ("import", mod + ("" if n.module else " " + ",".join(a.name for a in n.names)))))
            elif not n.level and (n.module or "").split(".")[0] not in safe_imports:
                found.append((n, ("import", mod)))
            found += [(n, ("import", f"{mod}.{a.name}")) for a in n.names if a.name in DANGEROUS_NAMES or _is_danger_attr(a.name)]
        elif isinstance(n, ast.Name):
            if n.id in DANGEROUS_NAMES:
                found.append((n, ("name", n.id)))
            elif _is_dunder(n.id):
                found.append((n, ("dunder", n.id)))
        elif isinstance(n, ast.Attribute):
            if _is_danger_attr(n.attr):
                found.append((n, ("attr", n.attr)))
            elif _is_dunder(n.attr):
                found.append((n, ("dunder", n.attr)))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            found += [(n, ("strdunder", d)) for d in set(_DUNDER.findall(n.value)) if d not in ALLOWED_DUNDERS]
            if _FMT_WALK.search(n.value):
                found.append((n, ("fmtwalk", n.value[:40])))
        elif isinstance(n, ast.Call):
            c = _callee(n)
            if c is not None and (c in DANGEROUS_NAMES or _is_danger_attr(c)):
                found.append((n, ("call", ast.dump(n))))
    prof: Counter = Counter(k for _, k in found)

    def unit_of(node):
        chain, cur = [], node
        while cur in parents:
            chain.append(cur)
            cur = parents[cur]
        funcs = [c for c in chain if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if funcs:
            return funcs[-1]
        stmts = [c for c in chain if isinstance(parents.get(c), (ast.Module, ast.ClassDef))]
        return stmts[0] if stmts else node
    units = {}
    for n, _ in found:
        u = unit_of(n)
        units[id(u)] = u
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    danger_calls = [n for n, k in found if k[0] == "call"]
    for _ in range(5):          # names that aim a dangerous call (its callee and arguments) or are passed to a dangerous function
        defs = {u.name for u in units.values() if isinstance(u, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        feeds = {x.id for c in danger_calls for x in ast.walk(c) if isinstance(x, ast.Name)}
        feeds |= {x.id for c in calls if _callee(c) in defs for x in ast.walk(c) if isinstance(x, ast.Name)}
        grew = False
        for st in tree.body:
            bound = ({t.id for t in (st.targets if isinstance(st, ast.Assign) else [st.target]) if isinstance(t, ast.Name)}
                     if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign)) else
                     {st.name} if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else set())
            if bound & feeds and id(st) not in units:
                units[id(st)] = st; grew = True
        if not grew:
            break
    frozen_names = set()
    for u in units.values():
        if isinstance(u, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            frozen_names.add(u.name)
        label = getattr(u, "name", "module-level statement")
        prof[("frozen", label, ast.dump(u))] += 1
    for n in ast.walk(tree):                                     # reaching frozen code: any new reference or call-site
        if isinstance(n, ast.Call) and _callee(n) in frozen_names:
            prof[("callsite", _callee(n), ast.dump(n))] += 1
        elif isinstance(n, ast.Name) and n.id in frozen_names:
            prof[("ref", n.id)] += 1
        elif isinstance(n, ast.Attribute) and n.attr in frozen_names:
            prof[("ref", n.attr)] += 1
    return prof


def _describe(key: tuple) -> str:
    kind = key[0]
    if kind == "frozen":
        return f"edits {key[1]!r}, which performs I/O or dynamic execution (such code is frozen)"
    if kind == "callsite":
        return f"adds or changes a call to {key[1]!r}, which performs I/O or dynamic execution"
    if kind == "ref":
        return f"adds a reference to {key[1]!r}, which performs I/O or dynamic execution"
    if kind == "call":
        return "adds or changes a call to a dangerous function"
    return f"{kind} {key[1]!r}"


def ast_gate(before: str, after: str, safe_imports=SAFE_IMPORTS) -> list[str]:
    """Reasons to reject the change before -> after ([] if it adds nothing dangerous)."""
    try:
        post = danger_profile(after, safe_imports)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as e:
        return [f"patched file does not parse ({type(e).__name__})"]
    try:
        pre = danger_profile(before, safe_imports)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        pre = Counter()                                          # a file that does not parse today has no trusted baseline
    return [f"introduces dangerous construct: {_describe(k)}" for k, v in post.items() if v > pre.get(k, 0)]


# ------------------------------------------------------------------------------------------------ policy and path checks
@dataclass
class Policy:
    allow: tuple = ("src/antslab/gyms/*.py",)
    deny: tuple = ("src/antslab/ledger.py", "src/antslab/gate.py", "src/antslab/runner.py",
                   "src/antslab/contract.py", "src/antslab/campaign.py", "src/antslab/registry.py",
                   "src/antslab/metrics.py", "src/antslab/judge.py", "src/antslab/maintainer.py",
                   "src/antslab/llm.py", "src/antslab/cli.py", "src/antslab/ui.py", "src/antslab/board.py",
                   "tests/*", "contracts/*", "deploy/*", "scripts/*", "requirements/*", "*.toml", "*.env")
    max_files: int = 2
    max_edits: int = 8
    max_changed_lines: int = 40
    max_edit_chars: int = 4000                                   # per old/new text; a single 5 MB line is not "a small patch"
    max_file_bytes: int = 1 << 20
    max_per_day: int = 1
    forbidden: tuple = ("subprocess", "socket", "urllib", "requests", "os.system", "eval(", "exec(", "__import__",
                        "ctypes", "shutil.rmtree", "os.remove", "os.unlink", "open(")


_BAD_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏  ‪-‮⁦-⁩﻿]")
_LONE_CR = re.compile(r"\r(?!\n)")


def _glob_one_level(rel: str, glob: str, case: bool) -> bool:
    """Like fnmatch, but '*' never crosses a '/': src/antslab/gyms/*.py must not match src/antslab/gyms/sub/x.py."""
    a, b = rel.split("/"), glob.split("/")
    f = fnmatch.fnmatchcase
    return len(a) == len(b) and all(f(x, y) if case else f(x.casefold(), y.casefold()) for x, y in zip(a, b))


def _exact_case(root: Path, rel: str) -> bool:
    cur = root
    try:
        for part in rel.split("/"):
            if part not in os.listdir(cur):
                return False
            cur = cur / part
    except OSError:
        return False
    return True


def resolve_target(root: Path, path, policy: Policy, must_be_allowed: bool = True) -> tuple[Path | None, str, str | None]:
    """(absolute path, repo-relative posix path, None) or (None, shown path, reason). Never raises."""
    shown = path if isinstance(path, str) and len(path) < 200 and "\x00" not in path else repr(path)[:80]
    if not isinstance(path, str) or not path or len(path) > 300 or "\x00" in path or "\\" in path or _BAD_CHARS.search(path):
        return None, shown, f"{shown}: not a usable relative path"
    if os.path.isabs(path) or path.startswith("~"):
        return None, shown, f"{shown}: escapes the repository (absolute path)"
    parts = path.split("/")
    if ".." not in parts:                                        # a symlink anywhere on the way is refused, not followed
        cur = root
        for part in parts:
            cur = cur / part
            if cur.is_symlink():
                return None, shown, f"{shown}: symlinks are not patchable"
    try:
        p = (root / path).resolve()
    except (OSError, ValueError, RuntimeError):
        return None, shown, f"{shown}: path cannot be resolved"
    if root not in p.parents:
        return None, shown, f"{shown}: escapes the repository"
    rel = p.relative_to(root).as_posix()
    if any(_glob_one_level(rel, g, False) or fnmatch.fnmatchcase(rel.casefold(), g.casefold()) for g in policy.deny):
        return None, rel, f"{rel}: protected (deny list)"
    if posixpath.normpath(path) != rel or "" in parts or "." in parts or ".." in parts:
        return None, rel, f"{shown}: path must be a normalised relative path"
    if must_be_allowed and not any(_glob_one_level(rel, g, True) for g in policy.allow):
        return None, rel, f"{rel}: not on the allow list"
    if not _exact_case(root, rel):
        return None, rel, f"{rel}: does not match the case of the file on disk"
    if not p.is_file():
        return None, rel, f"{rel}: does not exist"
    return p, rel, None


def _apply_edits(text: str, edits: list) -> tuple[str, list[str]]:
    """Apply edits one after the other; every 'old' must be unique in the text AS IT IS at that moment."""
    bad = []
    for e in edits:
        n = text.count(e.old) if e.old else 0
        if n != 1:
            bad.append(f"'old' text must occur exactly once (found {n})")
            continue
        text = text.replace(e.old, e.new, 1)
    return text, bad


def check_patch(patch: Patch, root: Path, policy: Policy) -> list[str]:
    root = Path(root).resolve()
    bad = []
    if not patch.edits:
        return ["empty patch"]
    if len(patch.edits) > policy.max_edits:
        return [f"{len(patch.edits)} edits (limit {policy.max_edits})"]
    if not all(isinstance(e, Edit) and isinstance(e.path, str) and isinstance(e.old, str) and isinstance(e.new, str) for e in patch.edits):
        return ["malformed edit"]
    if len({e.path for e in patch.edits}) > policy.max_files:
        bad.append(f"touches more than {policy.max_files} files")
    changed, by_file = 0, {}
    for e in patch.edits:
        p, rel, why = resolve_target(root, e.path, policy)
        if why:
            bad.append(why); continue
        for label, t in (("old", e.old), ("new", e.new)):
            if len(t) > policy.max_edit_chars:
                bad.append(f"{rel}: '{label}' text is {len(t)} characters (limit {policy.max_edit_chars})")
            elif _BAD_CHARS.search(t) or _LONE_CR.search(t):
                bad.append(f"{rel}: '{label}' text contains control, bidirectional or invisible characters")
            else:
                try:
                    t.encode("utf-8")
                except UnicodeEncodeError:
                    bad.append(f"{rel}: '{label}' text is not valid unicode")
        changed += max(e.old.count("\n"), e.new.count("\n")) + 1
        for tok in policy.forbidden:
            if tok in e.new and tok not in e.old:
                bad.append(f"{rel}: introduces forbidden construct {tok!r}")
        by_file.setdefault(rel, (p, []))[1].append(e)
    if changed > policy.max_changed_lines:
        bad.append(f"changes ~{changed} lines (limit {policy.max_changed_lines})")
    if bad:
        return bad
    for rel, (p, edits) in by_file.items():
        if p.stat().st_size > policy.max_file_bytes:
            bad.append(f"{rel}: larger than {policy.max_file_bytes} bytes"); continue
        try:
            before = p.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            bad.append(f"{rel}: not valid UTF-8 text"); continue
        if "\x00" in before:
            bad.append(f"{rel}: binary content"); continue
        after, errs = _apply_edits(before, edits)
        bad += [f"{rel}: {m}" for m in errs]
        if not errs:
            bad += [f"{rel}: {m}" for m in ast_gate(before, after)]
    return bad


def _write_atomic(path: Path, data: bytes) -> None:
    """Write next to the target and rename over it, keeping the file mode: a crash never leaves a half-written gym file."""
    mode = path.stat().st_mode & 0o777
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ants_tmp_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _sandbox(root: Path, patch: Patch) -> Path:
    sb = Path(tempfile.mkdtemp(prefix="ants_patch_"))
    try:
        shutil.copytree(root, sb, dirs_exist_ok=True, ignore_dangling_symlinks=True, ignore=shutil.ignore_patterns(
            ".venv", "runs", "internal", ".git", "__pycache__", ".pytest_cache", "*.sqlite*", "*.env", "node_modules"))
        policy = Policy()
        by_file: dict = {}
        for e in patch.edits:
            by_file.setdefault(e.path, []).append(e)
        for path, edits in by_file.items():
            f = sb / path
            text, errs = _apply_edits(f.read_bytes().decode("utf-8"), edits)
            if errs:
                raise ValueError(errs[0])
            f.write_bytes(text.encode("utf-8"))
    except BaseException:
        shutil.rmtree(sb, ignore_errors=True)
        raise
    return sb


def _child_env(cwd, extra_env=None) -> dict:
    """The patched code is untrusted: it gets a minimal environment, never the operator's (API keys, tokens, ...)."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(cwd), "PYTHONPATH": str(Path(cwd) / "src"),
           "PYTHONHASHSEED": "0", "PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}
    env.update(extra_env or {})
    return env


def _limits(timeout):
    def fn():
        resource.setrlimit(resource.RLIMIT_CPU, (int(timeout) + 30, int(timeout) + 60))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 << 20, 64 << 20))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return fn


def _run(cmd, cwd, timeout, extra_env=None, tail: int = 1500):
    """Run a command in its own process group with a clean environment and resource limits; never raises on timeout.
    Output goes to an unlinked temp file (a chatty child cannot exhaust our memory) and only its tail is returned."""
    with tempfile.TemporaryFile() as out:
        p = subprocess.Popen(cmd, cwd=cwd, env=_child_env(cwd, extra_env), stdin=subprocess.DEVNULL, stdout=out,
                             stderr=subprocess.STDOUT, start_new_session=True, preexec_fn=_limits(timeout))
        try:
            rc = p.wait(timeout=timeout)
            note = ""
        except subprocess.TimeoutExpired:
            rc, note = 124, f"timeout after {timeout}s\n"
        finally:
            try:
                os.killpg(p.pid, signal.SIGKILL)                 # also reaps stragglers a finished leader left behind
            except (ProcessLookupError, PermissionError):
                pass
            p.wait()
        size = out.seek(0, os.SEEK_END)
        out.seek(max(0, size - tail))
        return rc, (note + out.read().decode("utf-8", "replace"))[-tail:]


_GOLD = """
import json, sys, importlib, os
nonce, mod, fn, specs = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4])
f = getattr(importlib.import_module(mod), fn)
out = []
for s in specs:
    try: out.append({"ok": True, "m": f(s)})
    except Exception as e: out.append({"ok": False, "err": type(e).__name__})
sys.stdout.write("\\n" + nonce + json.dumps(out) + "\\n"); sys.stdout.flush()
os._exit(0)                                   # no atexit hooks / finalizers of the patched module may add a second 'result'
"""


def golden(root_a: Path, root_b: Path, module: str, func: str, specs: list, repair_specs: list, tol: float = 1e-9) -> list[str]:
    """specs that worked before must give identical metrics after; repair_specs must now work."""
    def go(r, sp):
        nonce = "@@GOLD" + os.urandom(8).hex() + "@@"
        rc, out = _run([sys.executable, "-c", _GOLD, nonce, module, func, json.dumps(sp)], r, 300, tail=1 << 22)
        for line in reversed(out.splitlines()):
            if line.startswith(nonce):
                try:
                    return json.loads(line[len(nonce):])
                except ValueError:
                    return None
        return None
    a, b, rep = go(root_a, specs), go(root_b, specs), go(root_b, repair_specs)
    if a is None or b is None or rep is None:
        return ["golden replay could not run"]
    bad = []
    for i, (x, y) in enumerate(zip(a, b)):
        if x["ok"] != y["ok"]:
            bad.append(f"golden spec {i}: success changed {x['ok']} -> {y['ok']}")
        elif x["ok"]:
            if not isinstance(x["m"], dict) or not isinstance(y["m"], dict):
                if x["m"] != y["m"]:
                    bad.append(f"golden spec {i}: result changed")
                continue
            for k, v in x["m"].items():
                w = y["m"].get(k)
                if isinstance(v, (int, float)) and not (isinstance(w, (int, float)) and abs(v - w) <= tol * max(1.0, abs(v))):
                    bad.append(f"golden spec {i}: metric {k} drifted {v} -> {w}")      # a missing or NaN metric is drift too
    bad += [f"repair spec {i} still fails ({r.get('err')})" for i, r in enumerate(rep) if not r["ok"]]
    return bad


class Maintainer:
    def __init__(self, ledger, root, backup_dir, policy: Policy = Policy(), test_cmd=None):
        self.l, self.root, self.backup, self.policy = ledger, Path(root).resolve(), Path(backup_dir), policy
        self.test_cmd = test_cmd or [sys.executable, "-m", "pytest", "-q", "-x", "-m", "not slow", "-p", "no:cacheprovider"]

    def _applied_last_day(self) -> int:
        cut = time.time() - 86400
        return sum(1 for e in self.l.events("patch_applied") if e["ts"] >= cut)

    def attempt(self, patch: Patch, module: str, func: str, specs: list, repair_specs: list) -> dict:
        pid = patch.digest()
        self.l.event("patch_proposed", {"id": pid, "model": str(patch.model)[:60], "rationale": str(patch.rationale)[:300],
                                        "files": sorted({str(e.path)[:200] for e in patch.edits})})

        def reject(why):
            self.l.event("patch_rejected", {"id": pid, "reasons": why})
            return {"applied": False, "id": pid, "reasons": why}
        bad = check_patch(patch, self.root, self.policy)
        if bad:
            return reject(bad)
        if self._applied_last_day() >= self.policy.max_per_day:
            return reject([f"rate limit: {self.policy.max_per_day} patch(es) per day already applied"])
        sb = None
        try:
            sb = _sandbox(self.root, patch)
            rc, out = _run(self.test_cmd, sb, 900)
            if rc != 0:
                return reject([f"tests fail in sandbox: {out[-300:]}"])
            bad = golden(self.root, sb, module, func, specs, repair_specs)
            if bad:
                return reject(bad)
            return self._apply(patch, pid)
        except Exception as e:                                   # a hostile patch must never take the daemon down
            return reject([f"patch could not be evaluated: {type(e).__name__}: {str(e)[:200]}"])
        finally:
            if sb is not None:
                shutil.rmtree(sb, ignore_errors=True)

    def _apply(self, patch: Patch, pid: str) -> dict:
        """Re-validate right before writing (the tree may have changed during the sandbox run), back up bytes, replace atomically."""
        bad = check_patch(patch, self.root, self.policy)
        if bad:
            raise ValueError("tree changed during evaluation: " + "; ".join(bad)[:200])
        by_file: dict = {}
        for e in patch.edits:
            by_file.setdefault(e.path, []).append(e)
        bk = self.backup / pid
        bk.mkdir(parents=True, exist_ok=True)
        manifest, new_texts = {}, {}
        for i, (path, edits) in enumerate(sorted(by_file.items())):
            raw = (self.root / path).read_bytes()
            new_texts[path], _ = _apply_edits(raw.decode("utf-8"), edits)
            (bk / f"f{i}").write_bytes(raw)
            manifest[f"f{i}"] = {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}
        (bk / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
        for path, text in new_texts.items():
            _write_atomic(self.root / path, text.encode("utf-8"))
        self.l.event("patch_applied", {"id": pid, "files": sorted(by_file), "model": str(patch.model)[:60],
                                       "rationale": str(patch.rationale)[:300]})
        return {"applied": True, "id": pid}

    def rollback(self, pid: str) -> list[str]:
        """Restore the exact bytes saved before patch `pid` was applied."""
        if not isinstance(pid, str) or not re.fullmatch(r"[0-9a-f]{16}", pid):
            raise ValueError("a patch id is 16 hex digits")
        bk = self.backup / pid
        try:
            manifest = json.loads((bk / "manifest.json").read_text())
        except (OSError, ValueError):
            raise ValueError(f"no usable backup manifest for patch {pid}") from None
        restored = []
        for name, meta in sorted(manifest.items()):
            if not re.fullmatch(r"f\d+", name):
                raise ValueError("corrupt backup manifest")
            target, rel, why = resolve_target(self.root, meta["path"], self.policy, must_be_allowed=False)
            if why:
                raise ValueError(f"refusing to restore: {why}")
            raw = (bk / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
                raise ValueError(f"backup of {rel} is corrupt (hash mismatch)")
            _write_atomic(target, raw)
            restored.append(rel)
        self.l.event("patch_rolled_back", {"id": pid, "files": restored})
        return restored

    def should_roll_back(self, since_ts: float, max_fail_rate: float = 0.5, min_n: int = 10) -> bool:
        """Watch the trials that ran AFTER the patch; roll back if the failure rate exploded."""
        rows = self.l.conn.execute("SELECT state FROM trials WHERE finished_at>? AND state IN ('done','failed','timeout','crashed')",
                                   (since_ts,)).fetchall()
        return len(rows) >= min_n and sum(r[0] != "done" for r in rows) / len(rows) > max_fail_rate


def draft_repair(client, failure_summary: str, files: dict[str, str]) -> Patch | None:
    """Ask a (weak) LLM for a minimal patch. Output is untrusted data: it still has to pass every gate."""
    src = "\n\n".join(f"### {p}\n{t[:6000]}" for p, t in files.items())
    obj, meta = client.chat_json([
        {"role": "system", "content": "You fix small bugs. Reply with ONLY JSON: {\"edits\":[{\"path\":...,\"old\":...,\"new\":...}],\"rationale\":...}. "
                                      "'old' must occur exactly once. Change as little as possible. Never touch evaluation semantics."},
        {"role": "user", "content": f"Failure:\n{failure_summary}\n\nFiles you may edit:\n{src}"}], temperature=0.2)
    if not obj or not isinstance(obj.get("edits"), list) or not 0 < len(obj["edits"]) <= 64:
        return None
    try:
        return Patch([Edit(str(e["path"]), str(e["old"]), str(e["new"])) for e in obj["edits"]],
                     str(obj.get("rationale", ""))[:300], str(meta.get("ep", ""))[:60])
    except (KeyError, TypeError):
        return None

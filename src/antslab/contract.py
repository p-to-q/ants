"""Task contract (task.toml) and the typed search space.

The space is deliberately small and closed: every candidate is a flat JSON spec whose keys, types
and ranges are declared here. Anything an LLM proposes is validated against it *strictly*
(no clamping), so malformed or out-of-range ideas are rejected and counted, never silently repaired.
"""
from __future__ import annotations

import math
import re
import string
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .ledger import canon
from .textsafe import clean_text


class SpecError(ValueError):
    pass


def _show(v, n: int = 40) -> str:
    """Short, printable, never-raising rendering of an untrusted value for an error message (str(10**5000) raises ValueError)."""
    try:
        if isinstance(v, int) and not isinstance(v, bool) and v.bit_length() > 64:
            return f"<int of {v.bit_length()} bits>"
        return clean_text(repr(v[:80] if isinstance(v, str) else v), n)
    except Exception:
        return f"<{type(v).__name__}>"


@dataclass
class Param:
    name: str
    kind: str                      # int | float | logfloat | cat | bool
    low: float | None = None
    high: float | None = None
    choices: list | None = None
    when: dict | None = None        # active only if every {other_param: [allowed values]} holds
    layer: str = ""                 # lifecycle layer, e.g. data / pretrain / architecture / optim / post
    doc: str = ""
    default: object = None
    role: str = ""                  # "skeleton": discrete structure that defines a niche (swept exhaustively)

    def default_value(self):
        if self.default is not None:
            return self.default
        if self.kind == "cat":
            return self.choices[0]
        if self.kind == "bool":
            return False
        if self.kind == "logfloat":
            return float(math.sqrt(self.low * self.high))
        mid = (self.low + self.high) / 2
        return int(round(mid)) if self.kind == "int" else float(mid)

    def to_unit(self, v) -> float:
        if self.kind == "logfloat":
            return (math.log(v) - math.log(self.low)) / (math.log(self.high) - math.log(self.low))
        return (v - self.low) / (self.high - self.low) if self.high > self.low else 0.0

    def from_unit(self, u):
        if u <= 0.0 and self.kind != "bool":
            return int(self.low) if self.kind == "int" else float(self.low)
        if u >= 1.0 and self.kind != "bool":
            return int(self.high) if self.kind == "int" else float(self.high)
        u = min(1.0, max(0.0, u))
        if self.kind == "logfloat":
            return self._clamp(math.exp(math.log(self.low) + u * (math.log(self.high) - math.log(self.low))))
        v = self.low + u * (self.high - self.low)
        return int(round(v)) if self.kind == "int" else self._clamp(v)

    def validate(self, v):
        k = self.kind
        if k == "bool":
            if not isinstance(v, bool):
                raise SpecError(f"{self.name}: expected bool")
            return v
        if k == "cat":
            for c in self.choices:                                 # exact type too: True is not 1, 1.0 is not 1 (they would hash alike)
                if type(v) is type(c) and v == c:
                    return c
            raise SpecError(f"{self.name}: {_show(v, 60)} not one of {len(self.choices)} choices")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise SpecError(f"{self.name}: expected number, got {type(v).__name__}")
        if isinstance(v, float) and not math.isfinite(v):
            raise SpecError(f"{self.name}: non-finite")
        if not (self.low <= v <= self.high):
            raise SpecError(f"{self.name}: {_show(v, 30)} outside [{self.low}, {self.high}]")
        if k == "int":
            if int(v) != v:
                raise SpecError(f"{self.name}: expected integer")
            return int(v)
        return float(v)

    def sample(self, rng):
        k = self.kind
        if k == "bool":
            return rng.random() < 0.5
        if k == "cat":
            return rng.choice(self.choices)
        if k == "int":
            return rng.randint(int(self.low), int(self.high))
        if k == "logfloat":
            return self._clamp(math.exp(rng.uniform(math.log(self.low), math.log(self.high))))
        return float(rng.uniform(self.low, self.high))

    def _clamp(self, v: float) -> float:
        """exp(log(x)) is not exact: keep generated values inside the declared closed interval."""
        return float(min(self.high, max(self.low, v)))

    def neighbor(self, v, rng, scale=0.15):
        k = self.kind
        if k in ("bool", "cat"):
            return self.sample(rng)
        if k == "int":
            span = max(1, int(round((self.high - self.low) * scale)))
            return int(min(self.high, max(self.low, v + rng.randint(-span, span))))
        if k == "logfloat":
            lo, hi = math.log(self.low), math.log(self.high)
            x = math.log(v) + rng.gauss(0, (hi - lo) * scale)
            return self._clamp(math.exp(min(hi, max(lo, x))))
        x = v + rng.gauss(0, (self.high - self.low) * scale)
        return float(min(self.high, max(self.low, x)))

    def dist(self, a, b) -> float:
        """Normalised distance in [0,1]."""
        if self.kind in ("bool", "cat"):
            return 0.0 if a == b else 1.0
        if self.kind == "logfloat":
            return abs(math.log(a) - math.log(b)) / (math.log(self.high) - math.log(self.low))
        return abs(a - b) / (self.high - self.low) if self.high > self.low else 0.0


@dataclass
class Space:
    params: dict = field(default_factory=dict)

    def is_active(self, name: str, spec: dict) -> bool:
        w = self.params[name].when
        return not w or all(spec.get(k) in vs for k, vs in w.items())

    def active_names(self, spec: dict) -> list:
        return [n for n in self.params if self.is_active(n, spec)]

    def canonical(self, spec: dict) -> dict:
        """Spec without dormant parameters: equivalent configurations collapse to one key."""
        return {n: spec[n] for n in self.active_names(spec)}

    def key(self, spec: dict) -> str:
        import hashlib
        return hashlib.sha256(canon(self.canonical(spec)).encode()).hexdigest()[:16]

    def validate(self, spec: dict) -> dict:
        if not isinstance(spec, dict):
            raise SpecError("spec must be an object")
        extra = set(spec) - set(self.params)
        missing = set(self.params) - set(spec)
        if extra:
            raise SpecError(f"unknown keys: {sorted(clean_text(k, 40) for k in extra)[:10]}")
        if missing:
            raise SpecError(f"missing keys: {sorted(missing)[:10]}")
        return {n: p.validate(spec[n]) for n, p in self.params.items()}

    def sample(self, rng) -> dict:
        return {n: p.sample(rng) for n, p in self.params.items()}

    def mutate(self, spec: dict, rng, k: int | None = None, scale=0.15, weights: dict | None = None) -> dict:
        """Mutate k ACTIVE parameters, chosen by `weights` (credit-assigned lever values) when given."""
        names = self.active_names(spec)
        k = min(k or rng.choice([1, 1, 2, 3]), len(names))
        out, pool = dict(spec), list(names)
        for _ in range(k):
            w = [max(1e-6, (weights or {}).get(n, 1.0)) for n in pool]
            n = rng.choices(pool, w)[0]
            pool.remove(n)
            out[n] = self.params[n].neighbor(spec[n], rng, scale)
        return out

    def changed(self, a: dict, b: dict) -> list:
        return [n for n in self.active_names(b) if a.get(n) != b.get(n)]

    def crossover(self, a: dict, b: dict, rng) -> dict:
        return {n: (a[n] if rng.random() < 0.5 else b[n]) for n in self.params}

    def distance(self, a: dict, b: dict) -> float:
        d = []
        for n, p in self.params.items():
            ia, ib = self.is_active(n, a), self.is_active(n, b)
            if ia and ib:
                d.append(p.dist(a[n], b[n]))
            elif ia or ib:
                d.append(1.0)
        return sum(d) / len(d) if d else 0.0

    def describe(self) -> str:
        lines = []
        for n, p in self.params.items():
            if p.kind in ("cat",):
                lines.append(f"- {n}: one of {p.choices}")
            elif p.kind == "bool":
                lines.append(f"- {n}: true|false")
            else:
                lines.append(f"- {n}: {p.kind} in [{p.low}, {p.high}]")
            if p.when:
                lines[-1] += f"   (only matters when {p.when})"
            if p.doc:
                lines[-1] += f"   -- {p.doc}"
        return "\n".join(lines)

    def skeleton_names(self) -> list:
        return [n for n, p in self.params.items() if p.role == "skeleton"]

    def niche(self, spec: dict) -> tuple:
        return tuple(spec[n] for n in self.skeleton_names())

    def defaults(self) -> dict:
        return {n: p.default_value() for n, p in self.params.items()}

    def layers(self) -> dict:
        out: dict = {}
        for n, p in self.params.items():
            out.setdefault(p.layer or "other", []).append(n)
        return out


@dataclass
class Contract:
    name: str
    gym: str
    python: str
    trial_timeout_s: int
    max_attempts: int
    unseal_limit: int
    primary: str
    direction: str
    cap_metric: str | None
    caps: list
    stages: list
    promote_quantile: float
    min_history: int
    commands: dict
    space: Space
    thresholds: dict
    code_paths: list
    env_passthrough: list
    full_record_stages: list
    stage_timeouts: dict
    static_cost: str | None
    static_mem: str | None
    mem_cap_mb: float
    stone_quota: float
    raw: dict

    def better(self, a: float, b: float) -> bool:
        return a < b if self.direction == "min" else a > b

    def worst(self) -> float:
        return float("inf") if self.direction == "min" else float("-inf")


# ---------------------------------------------------------------------------------------------- loading and validation
# A contract is configuration written by the operator, but it is parsed from a file that may come from anywhere (a pull request, a
# copied server, a hostile download). It is validated completely at load time and fails closed with a SpecError that names the field.
MAX_CONTRACT_BYTES = 1 << 20
MAX_PARAMS, MAX_CHOICES, MAX_SKELETON_CELLS = 128, 256, 100_000
COMMAND_FIELDS = frozenset({"python", "stage", "spec", "out", "seed", "attempt"})     # the only placeholders a command may use
COMMAND_NAMES = ("eval", "final")
PARAM_KEYS = frozenset({"type", "low", "high", "choices", "when", "layer", "doc", "default", "role"})
KINDS = ("int", "float", "logfloat", "cat", "bool")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_ENV = re.compile(r"[A-Z_][A-Z0-9_]{0,63}\Z")
_STAGE = re.compile(r"[A-Za-z0-9_]{1,32}\Z")
_REF = re.compile(r"antslab(\.[A-Za-z_][A-Za-z0-9_]*)+:[A-Za-z_][A-Za-z0-9_]*\Z")
_SECRETY = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH", re.I)
_UNSAFE_ENV = frozenset({"PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT", "PYTHONBREAKPOINT", "PYTHONUSERBASE",
                         "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "HOME", "IFS",
                         "BASH_ENV", "ENV", "SHELLOPTS", "PYTHONWARNINGS", "PYTHONIOENCODING", "PYTHONHASHSEED"})


def _fail(where: str, msg: str):
    raise SpecError(f"contract {where}: {msg}")


def _num(d: dict, key: str, default, lo: float, hi: float, where: str, integer: bool = False):
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        _fail(f"{where}.{key}", f"expected a finite number, got {_show(v)}")
    if integer and int(v) != v:
        _fail(f"{where}.{key}", f"expected an integer, got {v}")
    if not lo <= v <= hi:
        _fail(f"{where}.{key}", f"{v} is outside [{lo}, {hi}]")
    return int(v) if integer else float(v)


def _text(v, where: str, limit: int, allow_empty: bool = True) -> str:
    if not isinstance(v, str) or (not allow_empty and not v) or len(v) > limit or clean_text(v, limit + 1) != v:
        _fail(where, f"expected plain text of at most {limit} characters without control characters")
    return v


def _scalar(v, where: str):
    if isinstance(v, bool) or isinstance(v, int):
        return v
    if isinstance(v, float) and math.isfinite(v):
        return v
    if isinstance(v, str):
        return _text(v, where, 200)
    _fail(where, f"expected a string, number or boolean, got {_show(v)}")


def check_command(name: str, argv) -> list:
    """argv template list. Placeholders are plain `{python}`-style names from COMMAND_FIELDS: no attribute or index access
    (`{python.__class__}`), no conversions (`!r`), no format specs, no positional fields. Raises SpecError."""
    where = f"commands.{name}"
    if not isinstance(argv, list) or not 1 <= len(argv) <= 64:
        _fail(where, "expected a non-empty list of at most 64 strings (an argv, never a shell line)")
    for a in argv:
        if not isinstance(a, str) or len(a) > 4096 or "\x00" in a:
            _fail(where, "every element must be a string without NUL, at most 4096 characters")
        try:
            parts = list(string.Formatter().parse(a))
        except ValueError as e:
            _fail(where, f"malformed placeholder in {clean_text(a, 60)!r}: {e}")
        for _, field_name, spec, conv in parts:
            if field_name is None:
                continue
            if field_name not in COMMAND_FIELDS or spec or conv:
                _fail(where, f"placeholder in {clean_text(a, 60)!r} must be one of {sorted(COMMAND_FIELDS)} (no attributes, indexes, conversions or formats)")
    return list(argv)


def render_command(argv: list, mapping: dict) -> list:
    """Fill a validated template. The mapping may only hold plain strings."""
    if not all(isinstance(v, str) for v in mapping.values()) or set(mapping) - COMMAND_FIELDS:
        raise SpecError("command mapping must contain only the known placeholders, as plain strings")
    return [a.format(**mapping) for a in argv]


def _param(n: str, d, names: list) -> Param:
    where = f"space.{n}"
    if not isinstance(n, str) or not _NAME.match(n):
        _fail(where, "parameter names are 1-64 characters of [A-Za-z0-9_] not starting with a digit")
    if not isinstance(d, dict):
        _fail(where, "expected a table")
    unknown = set(d) - PARAM_KEYS
    if unknown:
        _fail(where, f"unknown keys {sorted(clean_text(k, 30) for k in unknown)}")
    kind = d.get("type")
    if kind not in KINDS:
        _fail(f"{where}.type", f"must be one of {KINDS}")
    low = high = choices = None
    if kind in ("int", "float", "logfloat"):
        lim = float(2 ** 53) if kind == "int" else 1e150
        low, high = _num(d, "low", None, -lim, lim, where, kind == "int"), _num(d, "high", None, -lim, lim, where, kind == "int")
        if not low < high:
            _fail(where, "low must be < high")
        if not math.isfinite(high - low):
            _fail(where, "range is too wide")
        if kind == "logfloat" and low <= 0:
            _fail(where, "logfloat needs low > 0")
        if "choices" in d:
            _fail(where, "choices only apply to type = \"cat\"")
    elif kind == "cat":
        choices = d.get("choices")
        if not isinstance(choices, list) or not 1 <= len(choices) <= MAX_CHOICES:
            _fail(f"{where}.choices", f"expected 1-{MAX_CHOICES} choices")
        choices = [_scalar(c, f"{where}.choices") for c in choices]
        if len(set(choices)) != len(choices):
            _fail(f"{where}.choices", "choices must be distinct (note that 1, 1.0 and true are the same value)")
        if "low" in d or "high" in d:
            _fail(where, "low/high do not apply to type = \"cat\"")
    elif {"low", "high", "choices"} & set(d):
        _fail(where, "low/high/choices do not apply to type = \"bool\"")
    when = d.get("when")
    if when is not None:
        if not isinstance(when, dict) or not when:
            _fail(f"{where}.when", "expected a table {param = [allowed values]}")
        for k, vs in when.items():
            if k == n or k not in names:
                _fail(f"{where}.when", f"{clean_text(k, 40)!r} is not another declared parameter")
            if not isinstance(vs, list) or not vs or len(vs) > MAX_CHOICES:      # a bare string would be matched as a substring
                _fail(f"{where}.when.{clean_text(k, 40)}", "expected a non-empty list of allowed values")
            [_scalar(x, f"{where}.when") for x in vs]
    role = d.get("role", "")
    if role not in ("", "skeleton"):
        _fail(f"{where}.role", "must be empty or \"skeleton\"")
    p = Param(n, kind, low, high, choices, when, _text(d.get("layer", ""), f"{where}.layer", 64), _text(d.get("doc", ""), f"{where}.doc", 500),
              d.get("default"), role)
    if p.default is not None:
        try:
            p.validate(p.default)
        except SpecError as e:
            _fail(f"{where}.default", str(e))
    return p


def _load_space(raw_space) -> Space:
    if not isinstance(raw_space, dict):
        _fail("space", "expected tables [space.<name>]")
    if len(raw_space) > MAX_PARAMS:
        _fail("space", f"{len(raw_space)} parameters (limit {MAX_PARAMS})")
    names = list(raw_space)
    params = {n: _param(n, d, names) for n, d in raw_space.items()}
    cells = 1
    for p in params.values():
        if p.role == "skeleton":
            if p.kind not in ("cat", "bool"):
                _fail(f"space.{p.name}.role", "a skeleton parameter must be discrete (cat or bool)")
            cells *= len(p.choices) if p.kind == "cat" else 2
    if cells > MAX_SKELETON_CELLS:
        _fail("space", f"the skeleton has {cells} cells (limit {MAX_SKELETON_CELLS}); it is swept exhaustively")
    return Space(params)


def _section(raw: dict, name: str, required: bool = True) -> dict:
    v = raw.get(name, None if required else {})
    if not isinstance(v, dict):
        _fail(name, "missing or not a table" if required else "not a table")
    return v


def load_contract(path) -> Contract:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_CONTRACT_BYTES:
            _fail("file", f"larger than {MAX_CONTRACT_BYTES} bytes")
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, RecursionError) as e:
        raise SpecError(f"contract {path.name}: cannot parse: {clean_text(e, 200)}") from None
    t, b, o = _section(raw, "task"), _section(raw, "budget"), _section(raw, "objective")
    c, disk, rec = _section(raw, "cascade", False), _section(raw, "disk", False), _section(raw, "record", False)
    name, gym = _text(t.get("name"), "task.name", 100, False), _text(t.get("gym"), "task.gym", 100, False)
    if not _NAME.match(gym):
        _fail("task.gym", "must be a module-style name ([A-Za-z0-9_])")
    stages = c.get("stages", ["s0"])
    if not isinstance(stages, list) or not 1 <= len(stages) <= 8 or not all(isinstance(x, str) and _STAGE.match(x) for x in stages) \
            or len(set(stages)) != len(stages):
        _fail("cascade.stages", "expected 1-8 distinct stage names of [A-Za-z0-9_]")
    stage_timeouts = b.get("stage_timeouts", {})
    if not isinstance(stage_timeouts, dict) or set(stage_timeouts) - set(stages):
        _fail("budget.stage_timeouts", f"must be a table keyed by declared stages {stages}")
    stage_timeouts = {k: _num(stage_timeouts, k, None, 1, 7 * 86400, "budget.stage_timeouts", True) for k in stage_timeouts}
    direction = o.get("direction", "min")
    if direction not in ("min", "max"):
        _fail("objective.direction", 'must be "min" or "max"')
    primary = _text(o.get("primary"), "objective.primary", 100, False)
    cap_metric, caps = o.get("cap_metric"), o.get("caps", [])
    if cap_metric is not None:
        _text(cap_metric, "objective.cap_metric", 100, False)
    if not isinstance(caps, list) or len(caps) > 64:
        _fail("objective.caps", "expected a list of at most 64 numbers")
    for x in caps:
        _num({"c": x}, "c", None, 0, 1e15, "objective.caps")        # validated; the original int/float is kept (it names the cap bins)
    if any(x <= 0 for x in caps) or caps != sorted(set(caps)):
        _fail("objective.caps", "caps must be positive, distinct and in increasing order")
    if bool(caps) != (cap_metric is not None):
        _fail("objective", "cap_metric and caps must be given together (a missing cap would silently remove the hard limit)")
    commands = _section(raw, "commands")
    if "eval" not in commands or set(commands) - set(COMMAND_NAMES):
        _fail("commands", f"must define eval and may only define {COMMAND_NAMES}")
    commands = {k: check_command(k, v) for k, v in commands.items()}
    for key in ("static_cost", "static_mem"):
        if o.get(key) is not None and not (isinstance(o[key], str) and _REF.match(o[key])):
            _fail(f"objective.{key}", "must look like antslab.module:function (only antslab modules are imported)")
    code_paths = t.get("code_paths", [])
    env = t.get("env_passthrough", [])
    if not isinstance(code_paths, list) or len(code_paths) > 64 or not all(isinstance(x, str) and x and "\x00" not in x for x in code_paths):
        _fail("task.code_paths", "expected a list of path strings")
    if not isinstance(env, list) or len(env) > 64 or not all(isinstance(x, str) and _ENV.match(x) for x in env):
        _fail("task.env_passthrough", "expected a list of upper-case environment variable names")
    for x in env:
        if x in _UNSAFE_ENV or x.startswith(("LD_", "DYLD_", "PYTHON")) or _SECRETY.search(x):
            _fail("task.env_passthrough", f"{x} may not be forwarded to evaluation processes (loader/interpreter control or a secret)")
    full = rec.get("full_stages", [stages[-1]])
    if not isinstance(full, list) or set(full) - set(stages):
        _fail("record.full_stages", "must be a list of declared stages")
    thr = {"warn": _num(disk, "warn_gb", 15, 0.001, 1e6, "disk"), "clean": _num(disk, "clean_gb", 12, 0.001, 1e6, "disk"),
           "pause": _num(disk, "pause_gb", 8, 0.001, 1e6, "disk"), "hardstop": _num(disk, "hardstop_gb", 6, 0.001, 1e6, "disk")}
    if not thr["warn"] > thr["clean"] > thr["pause"] > thr["hardstop"]:
        _fail("disk", "thresholds must satisfy warn_gb > clean_gb > pause_gb > hardstop_gb > 0")
    quantile = _num(c, "promote_quantile", 0.5, 0.0, 1.0, "cascade")
    if quantile <= 0:
        _fail("cascade.promote_quantile", "must be in (0, 1]")
    return Contract(
        name=name, gym=gym, python=_text(t.get("python", "python3"), "task.python", 200, False),
        trial_timeout_s=_num(b, "trial_timeout_s", 120, 1, 7 * 86400, "budget", True),
        max_attempts=_num(b, "max_attempts", 2, 1, 20, "budget", True), unseal_limit=_num(b, "unseal_limit", 1, 0, 100, "budget", True),
        primary=primary, direction=direction, cap_metric=cap_metric, caps=caps,
        stages=list(stages), promote_quantile=quantile, min_history=_num(c, "min_history", 8, 0, 10 ** 7, "cascade", True),
        commands=commands, space=_load_space(raw.get("space", {})), thresholds=thr,
        code_paths=[str((path.parent / p).resolve()) for p in code_paths], env_passthrough=list(env),
        full_record_stages=list(full), stage_timeouts=stage_timeouts,
        static_cost=o.get("static_cost"), static_mem=o.get("static_mem"), mem_cap_mb=_num(o, "mem_cap_mb", 800, 1, 1e7, "objective"),
        stone_quota=_num(o, "stone_quota", 0.05, 0.0, 1.0, "objective"), raw=raw)


def load_static_cost(ref: str | None):
    """'package.module:function' -> callable(spec)->float (a free, analytic cost such as parameter count), or None."""
    if not ref:
        return None
    import importlib
    mod, fn = ref.split(":")
    return getattr(importlib.import_module(mod), fn)


def code_fingerprint(paths) -> str:
    """Hash of the evaluator/gym source: frozen alongside the candidate."""
    import hashlib
    h = hashlib.sha256()
    for p in sorted(map(str, paths)):
        pp = Path(p)
        if not pp.exists():
            raise FileNotFoundError(f"code_paths entry does not exist: {pp} (an untracked evaluator would silently not start a new epoch)")
        files = sorted(pp.rglob("*.py")) if pp.is_dir() else [pp]
        for f in files:
            h.update(str(f.name).encode()); h.update(f.read_bytes())
    return h.hexdigest()


def spec_key(spec: dict) -> str:
    """Key of a raw spec (no dormant-parameter collapsing). Prefer Space.key()."""
    import hashlib
    return hashlib.sha256(canon(spec).encode()).hexdigest()[:16]

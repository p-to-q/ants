"""Data layer: catalog-driven loading, deterministic capped window sampling, role enforcement.

The catalog (data/catalog.toml) is the evaluation protocol: dataset roles are fixed there. Loading returns a `Series` with
chronological train/val/test boundaries; windows for large datasets are sampled deterministically (capped), so a 862-channel
dataset costs the same as a 7-channel one.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import tomllib
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROLES = ("dev", "confirm", "sealed", "stress")
_CACHE: dict = {}
_CATALOG_REF: dict = {}
ROLES_ENV = "ANTS_ALLOWED_ROLES"       # comma list; when set, Catalog.load() refuses datasets whose ACCESS role is not listed
_POLICY: list = []


class CatalogError(ValueError):
    """The catalog (the evaluation protocol) is malformed or was tampered with: refuse to start."""


class RoleError(PermissionError):
    """A dataset was requested by a stage that its role does not allow (e.g. sealed data during the search)."""


@contextmanager
def role_policy(allowed):
    """Within the block Catalog.load() fails closed for datasets whose access role is not in `allowed` (process-wide).
    Subprocesses inherit the policy through the ANTS_ALLOWED_ROLES environment variable instead (see active_policy)."""
    allowed = frozenset([allowed] if isinstance(allowed, str) else allowed)
    if not allowed <= set(ROLES):
        raise CatalogError(f"unknown roles {sorted(allowed - set(ROLES))}")
    _POLICY.append(allowed)
    try:
        yield allowed
    finally:
        _POLICY.pop()


def active_policy() -> frozenset | None:
    """The innermost role_policy() block, else ANTS_ALLOWED_ROLES, else None (unrestricted: tools, tests, `ants data`).
    A SET but empty/garbled variable denies everything (fail closed)."""
    if _POLICY:
        return _POLICY[-1]
    env = os.environ.get(ROLES_ENV)
    if env is None:
        return None
    toks = {t.strip() for t in env.split(",") if t.strip()}
    return frozenset(toks) if toks <= set(ROLES) else frozenset()


@dataclass
class Series:
    name: str
    family: str
    role: str
    values: np.ndarray            # (T, C) float64, raw (un-standardised)
    n_train: int
    n_val: int
    ref_period: int
    horizons: list
    source: str = ""

    @property
    def n_test(self) -> int:
        return len(self.values) - self.n_train - self.n_val

    def standardised(self) -> np.ndarray:
        mu, sd = self.values[: self.n_train].mean(0), self.values[: self.n_train].std(0) + 1e-8
        return (self.values - mu) / sd


# ---------------------------------------------------------------- parsers
def _csv_numeric(text: str, skip_first: int = 1) -> tuple[list, np.ndarray]:
    rows = list(csv.reader(io.StringIO(text)))
    cols = rows[0][skip_first:]
    return cols, np.array([[float(x) for x in r[skip_first:]] for r in rows[1:]], dtype=np.float64)


def _zip_texts(path: Path) -> list[str]:
    out = []
    with zipfile.ZipFile(path) as z:
        for n in sorted(z.namelist()):
            if n.endswith("/") or n.startswith("/") or ".." in n.split("/"):
                continue
            out.append(z.read(n).decode("latin-1"))
    return out


def _ffill(v: np.ndarray) -> tuple[np.ndarray, int]:
    """Forward-fill NaNs per channel (leading NaNs take the first valid value). Returns (filled, n_filled)."""
    v = v.copy()
    n = int(np.isnan(v).sum())
    for c in range(v.shape[1]):
        col = v[:, c]
        bad = np.isnan(col)
        if bad.all():
            col[:] = 0.0
            continue
        idx = np.where(~bad, np.arange(len(col)), 0)
        np.maximum.accumulate(idx, out=idx)
        col[:] = col[idx]
        first = np.flatnonzero(~bad)[0]
        col[:first] = col[first]
    return v, n


def _num(x: str, comma: bool = False) -> float:
    x = x.strip().strip('"')
    if comma:
        x = x.replace(",", ".")
    try:
        return float(x)
    except ValueError:
        return float("nan")


def _zip_member(path: Path, suffix: str) -> bytes:
    import gzip as _gz
    with zipfile.ZipFile(path) as z:
        for n in sorted(z.namelist()):
            if n.endswith(suffix) and not n.endswith("/") and ".." not in n.split("/"):
                b = z.read(n)
                return _gz.decompress(b) if n.endswith(".gz") else b
    raise FileNotFoundError(suffix)


def parse(fmt: str, raw: Path, clean_sentinels: bool = False, meta: dict | None = None) -> np.ndarray:
    if fmt == "chronos_parquet":               # curated collection: one parquet row per series (id, timestamp, target...)
        import pyarrow.parquet as pq
        col = (meta or {}).get("target_column", "target")
        t = pq.read_table(raw)
        series = [np.array([np.nan if x is None else float(x) for x in row], dtype=np.float64) for row in t.column(col).to_pylist()]
        k, max_rows = int((meta or {}).get("max_series", 16)), int((meta or {}).get("max_rows", 20000))
        series.sort(key=len, reverse=True)
        series = series[:k]                                          # the K longest series (stable, no randomness)
        n = min(min(len(x) for x in series), max_rows)
        v = np.stack([x[-n:] for x in series], 1)                    # most recent n points; channels are NOT time-aligned across series
        return _ffill(v)[0] if np.isnan(v).any() else v
    if fmt == "synth_real":                    # surrogate of a REAL dataset (spectrum / marginal / linear-dynamics preserved)
        from . import synth_suite as SS
        base = ((meta or {}).get("_catalog") or _CATALOG_REF["cat"])._load(meta["synth_base"]).values[:20000, :8]
        base = base[:, :min(8, base.shape[1])]
        return SS.REAL_BASED[meta["synth_kind"]](base, np.random.default_rng(int(meta["synth_seed"])))
    if fmt == "synth":                         # generated, byte-reproducible from (family, seed)
        from .synth_suite import generate
        x, _ = generate(meta["synth_family"], int(meta["synth_seed"]))
        return _ffill(x)[0] if np.isnan(x).any() else x
    if fmt == "uci_airquality":                # ';' separated, decimal comma, -200 = missing
        rows = [r for r in csv.reader(io.StringIO(_zip_member(raw, "AirQualityUCI.csv").decode("latin-1")), delimiter=";") if r and r[0]]
        v = np.array([[_num(x, True) for x in r[2:15]] for r in rows[1:]])
        v[v <= -199] = np.nan
        return _ffill(v)[0]
    if fmt == "uci_appliances":
        rows = list(csv.reader(io.StringIO(_zip_member(raw, "energydata_complete.csv").decode("latin-1"))))
        return np.array([[_num(x) for x in r[1:]] for r in rows[1:]])
    if fmt == "uci_bike":                      # hourly rows; calendar categoricals dropped, target (cnt) last
        rows = list(csv.reader(io.StringIO(_zip_member(raw, "hour.csv").decode("latin-1"))))
        cols = rows[0]
        keep = [cols.index(c) for c in ("temp", "atemp", "hum", "windspeed", "casual", "registered", "cnt")]
        return np.array([[float(r[i]) for i in keep] for r in rows[1:]])
    if fmt == "uci_household":                 # 1-minute data -> hourly means (missing '?' ignored, then forward-filled)
        txt = _zip_member(raw, "household_power_consumption.txt").decode("latin-1").splitlines()
        v = np.array([[_num(x) for x in ln.split(";")[2:]] for ln in txt[1:]], dtype=np.float64)
        n = (len(v) // 60) * 60
        blocks = v[:n].reshape(-1, 60, v.shape[1])
        with np.errstate(all="ignore"):
            hourly = np.nanmean(np.where(np.isnan(blocks), np.nan, blocks), axis=1)
        return _ffill(hourly)[0]
    if fmt == "uci_metro":                     # nested gz; duplicate timestamps collapsed, irregular gaps kept (documented)
        rows = list(csv.reader(io.StringIO(_zip_member(raw, ".csv.gz").decode("latin-1"))))
        cols = rows[0]; ts = cols.index("date_time")
        keep = [cols.index(c) for c in ("temp", "rain_1h", "snow_1h", "clouds_all", "traffic_volume")]
        seen, out = set(), []
        for r in rows[1:]:
            if r[ts] in seen:
                continue
            seen.add(r[ts]); out.append([float(r[i]) for i in keep])
        return np.array(out)
    if fmt == "sunspot_daily":                 # ';' separated; col 4 = total sunspot number; -1 = missing
        v = np.array([[_num(ln.split(";")[4])] for ln in raw.read_text().splitlines() if ln.strip()])
        v[v < 0] = np.nan
        return _ffill(v)[0]
    if fmt == "sunspot_monthly":
        v = np.array([[_num(ln.split(";")[3])] for ln in raw.read_text().splitlines() if ln.strip()])
        v[v < 0] = np.nan
        return _ffill(v)[0]
    if fmt == "m4_hourly":                     # 414 independent series of unequal length -> first 700 points each, as channels
        rows = list(csv.reader(io.StringIO(raw.read_text())))[1:]
        return np.array([[_num(x) for x in r[1:701]] for r in rows]).T
    if fmt == "csv_last_col":                  # date,value
        rows = list(csv.reader(io.StringIO(raw.read_text())))[1:]
        return _ffill(np.array([[_num(r[-1])] for r in rows]))[0]
    if fmt == "ett_csv":
        return _csv_numeric(raw.read_text())[1]
    if fmt == "lstnet_gz":
        return np.loadtxt(gzip.open(raw), delimiter=",")
    if fmt == "mpi_roof_zip":
        parts = []
        for z in sorted(raw.glob("mpi_roof_2020*.zip")):
            parts.append(_csv_numeric(_zip_texts(z)[0])[1])
        v = np.concatenate(parts)
        if clean_sentinels:
            v = v.copy()
            for c in range(v.shape[1]):
                bad = v[:, c] <= -9990
                for i in np.flatnonzero(bad):
                    v[i, c] = v[i - 1, c] if i > 0 and not bad[i - 1] else (v[i - 1, c] if i > 0 else np.nan)
            if np.isnan(v).any():
                v = np.where(np.isnan(v), np.nanmean(v, 0), v)
        return v
    if fmt == "ili_zip":
        return _csv_numeric(_zip_texts(raw)[0])[1]
    raise ValueError(fmt)


# ---------------------------------------------------------------- catalog
def locate_catalog() -> Path:
    """Find data/catalog.toml: $ANTS_CATALOG, then $ANTS_HOME/data, then upwards from the current directory, then next to the source
    tree (editable installs), then ~/.ants/data. A wheel install has no repository, so the data directory must be findable."""
    import os
    cands = []
    if os.environ.get("ANTS_CATALOG"):
        cands.append(Path(os.environ["ANTS_CATALOG"]))
    if os.environ.get("ANTS_HOME"):
        cands.append(Path(os.environ["ANTS_HOME"]) / "data" / "catalog.toml")
    here = Path.cwd().resolve()
    cands += [d / "data" / "catalog.toml" for d in (here, *here.parents)]
    cands += [Path(__file__).resolve().parents[2] / "data" / "catalog.toml", Path.home() / ".ants" / "data" / "catalog.toml"]
    for c in cands:
        if c.exists():
            return c
    raise FileNotFoundError("data/catalog.toml not found; set ANTS_CATALOG or ANTS_HOME (searched: " + ", ".join(str(c) for c in cands[:4]) + " ...)")


class Catalog:
    def __init__(self, path):
        self.path = Path(path)
        self.base = self.path.parent
        _CATALOG_REF['cat'] = self
        d = tomllib.loads(self.path.read_text())
        self.defaults = d.get("defaults", {})
        self.meta = dict(d["dataset"])
        extra = self.base / "catalog_synth.toml"              # generated synthetic suite (role=stress), same format
        if extra.exists():
            syn = tomllib.loads(extra.read_text())["dataset"]
            clash = sorted(set(syn) & set(self.meta))
            if clash:                                          # an add-on file must never redefine (re-role) a protocol dataset
                raise CatalogError(f"{extra.name} redefines datasets of {self.path.name}: {clash}")
            bad = sorted(n for n, m in syn.items() if m.get("role") != "stress")
            if bad:
                raise CatalogError(f"{extra.name}: generated datasets must have role 'stress': {bad}")
            self.meta.update(syn)
        for n, m in self.meta.items():
            if m.get("role") not in ROLES:
                raise CatalogError(f"dataset {n}: role {m.get('role')!r} not in {ROLES}")
            if "family" not in m:
                raise CatalogError(f"dataset {n}: no family")
            if m.get("synth_base") is not None and m["synth_base"] not in self.meta:
                raise CatalogError(f"dataset {n}: synth_base {m['synth_base']!r} is not in the catalog")

    def names(self, role: str | None = None) -> list[str]:
        return [n for n, m in self.meta.items() if role is None or m["role"] == role]

    # ---- role enforcement -----------------------------------------------------------------------------------------
    def access_role(self, name: str) -> str:
        """The role that governs who may USE a dataset. A surrogate (synth_real: spectrum / marginal / dynamics of a real series)
        is a function of its base, so it inherits the base's role when that is sealed or confirm: a 'stress' dataset built from
        sealed electricity would otherwise hand sealed information to every stress diagnostic. Surrogates of dev data stay stress."""
        m = self.meta[name]
        base, seen = m.get("synth_base"), {name}
        while base is not None:
            if base in seen:
                raise CatalogError(f"synth_base cycle at {base}")
            seen.add(base)
            if self.meta[base]["role"] in ("sealed", "confirm"):
                return self.meta[base]["role"]
            base = self.meta[base].get("synth_base")
        return m["role"]

    def require_roles(self, names, allowed) -> list[str]:
        """Fail closed: every name must exist and have an access role in `allowed`; returns the names as a list.
        Call it with ('dev',) in every search stage, ('confirm',) in E2, ('stress',) in diagnostics, and let only gate.seal
        pass 'sealed'. An unknown name is refused, never skipped."""
        allowed = {allowed} if isinstance(allowed, str) else set(allowed)
        if not allowed <= set(ROLES):
            raise CatalogError(f"unknown roles {sorted(allowed - set(ROLES))}")
        names = list(names)
        bad = {n: (self.access_role(n) if n in self.meta else "unknown dataset") for n in names
               if n not in self.meta or self.access_role(n) not in allowed}
        if bad:
            raise RoleError(f"datasets not allowed here (allowed roles {sorted(allowed)}): {bad}")
        return names

    def names_for(self, allowed) -> list[str]:
        """Dataset names whose ACCESS role (surrogates inherit) is in `allowed`."""
        allowed = {allowed} if isinstance(allowed, str) else set(allowed)
        return [n for n in self.meta if self.access_role(n) in allowed]

    def protocol_hash(self) -> str:
        """Identity of the evaluation protocol: roles, splits, horizons (catalog files) and the raw-data manifest. Put it in the
        epoch identity: changing a role or swapping a data file must start a new epoch."""
        h = hashlib.sha256(self.PARSER_VERSION.encode())
        for f in (self.path, self.base / "catalog_synth.toml", self.base / "MANIFEST.sha256"):
            h.update(f.name.encode())
            if f.exists():
                h.update(f.read_bytes())
        return h.hexdigest()

    def load(self, name: str) -> Series:
        """Policy-checked entry point (see role_policy / ANTS_ALLOWED_ROLES); internal readers use _load."""
        pol = active_policy()
        if pol is not None and (name not in self.meta or self.access_role(name) not in pol):
            raise RoleError(f"dataset {name!r} (access role {self.access_role(name) if name in self.meta else '?'}) "
                            f"is not allowed in this process (allowed {sorted(pol)})")
        return self._load(name)

    def _load(self, name: str) -> Series:
        """Processed (unified) file if present, else parse the raw files. Both paths give the same Series. The role comes from the
        CATALOG; a processed file whose baked-in role/family disagrees with it is stale and ignored."""
        if name in _CACHE:
            return _CACHE[name]
        pz = self.base / "processed" / f"{name}.npz"
        if pz.exists():
            d = np.load(pz)
            meta = json.loads(str(d["meta"]))
            m = self.meta[name]
            if meta.get("parser_version") == self.PARSER_VERSION and meta.get("role") == m["role"] \
                    and meta.get("family") == m["family"]:
                v = d["values"].astype(np.float64)
                sp = meta["split"]
                s = Series(name, meta["family"], m["role"], v, int(sp[0]), int(sp[1]), int(meta["ref_period"]),
                           list(meta["horizons"]), meta.get("source", ""))
                _CACHE[name] = s
                return s
        return self._load_raw(name)

    def _load_raw(self, name: str) -> Series:
        m = self.meta[name]
        raw = self.base / m["raw"]
        v = parse(m["format"], raw, bool(m.get("clean_sentinels", False)), {**m, "_catalog": self})
        if m.get("use_rows"):                      # e.g. ETT: the standard protocol uses only the first 20 months
            v = v[: int(m["use_rows"])]
        T = len(v)
        sp = m["split"]
        ntr, nva = (int(sp[0]), int(sp[1])) if sp[0] > 1 else (int(T * sp[0]), int(T * sp[1]))
        s = Series(name, m["family"], m["role"], v, ntr, nva, int(m["ref_period"]),
                   list(m.get("horizons", self.defaults.get("horizons", [96, 192, 336, 720]))), m.get("source", ""))
        _CACHE[name] = s
        return s

    # ---- unified processed format: data/processed/<name>.npz -------------------------------------------------
    # arrays: values (T,C) float32 | columns (str) ; meta (json): name, family, role, ref_period, horizons, split, n_missing_filled,
    # source, license, raw files + sha256, parser version. float32 is the storage precision (relative error ~6e-8).
    PARSER_VERSION = "1"

    def build_processed(self, names=None, out_dir=None) -> list[dict]:
        out = Path(out_dir or self.base / "processed"); out.mkdir(parents=True, exist_ok=True)
        report = []
        for n in (names or self.names()):
            m = self.meta[n]
            _CACHE.pop(n, None)
            s = self._load_raw(n)
            v = s.values
            raw = self.base / m["raw"]
            files = [] if m["format"] == "synth" else (sorted(raw.glob("*")) if raw.is_dir() else [raw])
            meta = {"name": n, "family": m["family"], "role": m["role"], "ref_period": s.ref_period, "horizons": s.horizons,
                    "split": [s.n_train, s.n_val, s.n_test], "source": m.get("source", ""), "license": m.get("license", ""),
                    "note": m.get("note", ""), "format": m["format"], "parser_version": self.PARSER_VERSION,
                    "synth": ({"family": m["synth_family"], "seed": m["synth_seed"]} if m["format"] == "synth" else None),
                    "raw": {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files if f.is_file()}}
            path = out / f"{n}.npz"
            np.savez_compressed(path, values=v.astype(np.float32), meta=json.dumps(meta))
            const = int((v.std(0) == 0).sum())
            report.append({"name": n, "shape": v.shape, "nan": int(np.isnan(v).sum()), "constant_channels": const,
                           "min": float(np.nanmin(v)), "max": float(np.nanmax(v)), "role": m["role"], "family": m["family"],
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
        (out / "PROCESSED.json").write_text(json.dumps(report, indent=1))
        return report

    def verify_manifest(self, manifest: Path | None = None, only_existing: bool = False) -> list[str]:
        """Re-hash every raw file against data/MANIFEST.sha256; returns problems (empty = intact)."""
        mf = Path(manifest or self.base / "MANIFEST.sha256")
        bad = []
        for line in mf.read_text().splitlines():
            h, f = line.split(None, 1)
            p = self.base / f.strip()
            if not p.exists():
                if not only_existing:
                    bad.append(f"missing {f}")
            elif hashlib.sha256(p.read_bytes()).hexdigest() != h:
                bad.append(f"hash mismatch {f}")
        return bad


# ---------------------------------------------------------------- windows
def sample_windows(data: np.ndarray, lo: int, hi: int, L: int, H: int, cap: int | None, seed: int,
                   stride: int = 1, with_t: bool = False):
    """Windows whose TARGET lies in [lo, hi). If more than `cap` exist, a deterministic uniform sample over
    (channel, start) pairs is returned (sorted for reproducibility)."""
    starts = np.arange(max(lo, L), hi - H + 1, stride)
    C = data.shape[1]
    total = len(starts) * C
    if total == 0:
        raise ValueError("no windows: series too short for this lookback/horizon")
    if cap is not None and total > cap:
        flat = np.sort(np.random.default_rng(seed).choice(total, size=cap, replace=False))
    else:
        flat = np.arange(total)
    ch, si = flat // len(starts), flat % len(starts)
    idx = starts[si][:, None] + np.arange(-L, H)[None, :]
    w = data[idx, ch[:, None]]
    out = (w[:, :L], w[:, L:])
    return out + (((starts[si] - lo) / max(1, hi - lo)),) if with_t else out

"""Experiment registry: load, validate and query deploy/experiments.toml.

The registry is the source of truth for which experiments exist, what question each answers,
and what their parameters are.  It is NOT part of the ledger: it describes intent, and the
ledger records facts.  `ants experiments` compares the two.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

VALID_NAME = re.compile(r"^[a-z0-9_]{1,40}$")
VALID_STATUS = {"planned", "running", "finished", "archived"}
REQUIRED = {"name", "arm", "seed", "question", "status"}
OPTIONAL = {"contract", "cpu_hours"}
ALL_KEYS = REQUIRED | OPTIONAL


class RegistryError(ValueError):
    pass


def load_experiments(path: str | Path) -> list[dict]:
    """Parse and validate experiments.toml.  Returns a list of experiment dicts."""
    path = Path(path)
    if not path.exists():
        raise RegistryError(f"registry not found: {path}")
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    exps = raw.get("experiment", [])
    if not isinstance(exps, list):
        raise RegistryError("experiments.toml must contain [[experiment]] entries")
    seen_names: set[str] = set()
    out: list[dict] = []
    for i, e in enumerate(exps):
        if not isinstance(e, dict):
            raise RegistryError(f"experiment {i}: not a table")
        unknown = set(e) - ALL_KEYS
        if unknown:
            raise RegistryError(f"experiment {i} ({e.get('name', '?')}): unknown keys {unknown}")
        missing = REQUIRED - set(e)
        if missing:
            raise RegistryError(f"experiment {i}: missing keys {missing}")
        name = e["name"]
        if not VALID_NAME.match(name):
            raise RegistryError(f"experiment {i}: bad name {name!r} (must match {VALID_NAME.pattern})")
        if name in seen_names:
            raise RegistryError(f"experiment {i}: duplicate name {name!r}")
        seen_names.add(name)
        if e["status"] not in VALID_STATUS:
            raise RegistryError(f"experiment {name}: status {e['status']!r} not in {VALID_STATUS}")
        if not isinstance(e["seed"], int):
            raise RegistryError(f"experiment {name}: seed must be an integer")
        out.append(dict(e))
    return out


def running_experiments(path: str | Path) -> list[dict]:
    """Return only experiments with status='running'."""
    return [e for e in load_experiments(path) if e["status"] == "running"]


def arm_definition_hash(arm: str, contract_path: str | Path, seed: int) -> str:
    """Compute the arm_def_hash the same way Campaign.start_record does, WITHOUT starting a campaign.

    This imports the contract and proposer machinery but does not open a ledger or start a loop.
    """
    from .contract import load_contract
    from .proposers import make_proposer

    c = load_contract(contract_path)
    # make_proposer for LLM arms needs a client; for the hash we only need the operators list
    # which is determined by the arm name and contract, not by the client.  For arms that need
    # a client, we build a stub.
    client = None
    if arm in ("llm", "llm_dream", "portfolio_llm", "portfolio_dream", "portfolio_agent", "agent"):
        client = _StubClient()
    try:
        p = make_proposer(arm, c, seed, client)
    except Exception:
        return ""
    # replicate Campaign.arm_definition using the shared constant
    from .campaign import DEFAULT_BATCH
    ops = sorted(getattr(p, "ops", {}) or [getattr(p, "name", "?")])
    arm_def = {
        "arm": arm,
        "operators": ops,
        "floors": dict(sorted((getattr(p, "floors", {}) or {}).items())),
        "promote_quantile": c.promote_quantile,
        "promote_by_cap": bool(c.raw.get("cascade", {}).get("promote_by_cap")),
        "min_history": c.min_history,
        "batch": DEFAULT_BATCH,
    }
    return hashlib.sha256(json.dumps(arm_def, sort_keys=True).encode()).hexdigest()[:12]


class _StubClient:
    """Minimal stub so make_proposer can build the proposer graph without a real LLM endpoint."""
    def __getattr__(self, name):
        return self
    def __call__(self, *a, **kw):
        return None, {}


def check_experiment(exp: dict, base_dir: str | Path) -> dict:
    """Check one experiment against its workdir's ledger.  Returns a status dict."""
    base = Path(base_dir)
    name = exp["name"]
    workdir = base / name
    ledger_path = workdir / "ledger.sqlite"
    result = {"name": name, "arm": exp["arm"], "seed": exp["seed"], "status": exp["status"],
              "question": exp["question"], "ledger_exists": ledger_path.exists(),
              "last_hash": None, "current_hash": None, "match": None}
    if ledger_path.exists():
        from .ledger import Ledger
        try:
            led = Ledger.open_readonly(ledger_path)
            starts = led.recent_events("daemon_started", 1)
            if starts:
                result["last_hash"] = starts[-1]["payload"].get("arm_def_hash")
            led.close()
        except Exception:
            pass
    # compute the current hash from the code
    contract = exp.get("contract", "tinyts_real")
    # find the contract file
    from .cli import _contract_path
    try:
        cp = _contract_path(contract)
        result["current_hash"] = arm_definition_hash(exp["arm"], cp, exp["seed"])
    except Exception:
        pass
    if result["last_hash"] is not None and result["current_hash"]:
        result["match"] = result["last_hash"] == result["current_hash"]
    return result

"""Evidence gate: freeze first, then spend a sealed-evaluation credit, then run.

Order matters: the credit is spent BEFORE the evaluator runs, so a crash mid-run cannot be used to
get a second look at the sealed data. The evaluator code must hash to what was frozen.

Credit rule (see Ledger.record_unseal): one look per frozen candidate; `unseal_limit` looks per (gym, epoch, sealed set);
`unseal_lifetime` (default 3 x unseal_limit) per (gym, sealed set) over all epochs; every look and its result are
hash-chained events keyed by the freeze hash. The sealed set is the list of sealed datasets the final run may touch:
declare it in the contract as `[sealed] datasets = "catalog:sealed"` (every sealed dataset of data/catalog.toml, including
surrogates derived from them) or as an explicit list; without a declaration the whole gym is one set.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from .contract import Contract, code_fingerprint
from .ledger import GateError, Ledger, canon, sha256_text
from .runner import run_command
from .textsafe import clean_text

SEALED_ROLES = "dev,confirm,sealed"        # what a final run may read: never stress data


def contract_hash(c: Contract) -> str:
    """Same formula as Campaign._epoch_info()['contract']."""
    return hashlib.sha256(json.dumps(c.raw, sort_keys=True, default=str).encode()).hexdigest()[:16]


def sealed_set_id(c: Contract, catalog=None) -> str:
    if "sealed" not in c.raw:
        return ""                                   # no [sealed] declaration: the whole gym is one set
    sealed = c.raw["sealed"]
    # A contract built programmatically never went through load_contract, so the same rules are
    # enforced here and fail closed: a malformed declaration must never yield a silent (empty) set
    # identity, which would quietly make the whole gym one unbounded sealed set. (TOML has no null,
    # so a None here can only be a programmatic mutation.)
    if not isinstance(sealed, dict):
        raise GateError(f"[sealed] must be a table, got {clean_text(sealed, 60)!r}")
    decl = sealed.get("datasets")
    if decl is None:
        raise GateError("[sealed] datasets is required when [sealed] is present")
    if decl == "catalog:sealed":
        from .data import Catalog, locate_catalog      # lazy: data needs numpy, the search loop must not
        cat = catalog or Catalog(locate_catalog())
        return sha256_text(canon([[n, cat.meta[n]] for n in sorted(cat.names_for("sealed"))]))[:16]
    if isinstance(decl, list) and decl and all(isinstance(x, str) and x for x in decl):
        return sha256_text(canon(sorted(decl)))[:16]
    raise GateError(f"[sealed] datasets must be 'catalog:sealed' or a non-empty list of strings, got {clean_text(decl, 60)!r}")


def seal(ledger: Ledger, c: Contract, workdir, freeze_hash: str, seed: int = 12345,
         python: str | None = None, catalog=None, anchor_file=None) -> dict:
    """Run the one-shot sealed evaluation of a frozen candidate. Refuses (before anything is spent) if the event chain is broken
    or, when `anchor_file` (an append-only file of Ledger.write_anchor lines, kept off-box) is given, if the ledger no longer
    contains every anchored head: a truncated or rewritten ledger could otherwise forget earlier looks. A fresh anchor is
    appended after the result is recorded."""
    ok, bad = ledger.verify_chain()
    if not ok:
        raise GateError(f"event chain broken at seq {bad}; refusing sealed evaluation")
    if anchor_file is not None and Path(anchor_file).exists():
        ok, why = ledger.verify_anchor_file(anchor_file)
        if not ok:
            raise GateError(f"ledger disagrees with its external anchors ({why}); refusing sealed evaluation")
    f = ledger.frozen(c.gym, freeze_hash)
    if f is None:
        raise GateError("candidate was never frozen; refusing sealed evaluation")
    if code_fingerprint(c.code_paths) != f["code"]:
        raise GateError("evaluator/gym code changed after the candidate was frozen")
    if f.get("epoch"):                                       # the measuring epoch must still be today's evaluator + contract
        e = ledger.conn.execute("SELECT info FROM epochs WHERE id=?", (f["epoch"],)).fetchone()
        if e is not None and json.loads(e["info"]).get("contract") not in (None, contract_hash(c)):
            raise GateError("contract changed after the candidate was frozen (new epoch): freeze again")
    if f.get("protocol"):
        from .data import Catalog, locate_catalog      # lazy: data needs numpy, the search loop must not
        cat = catalog or Catalog(locate_catalog())
        if cat.protocol_hash() != f["protocol"]:
            raise GateError("data protocol (catalog roles/splits/manifest) changed after the candidate was frozen")
    sealed_set = sealed_set_id(c, catalog)
    lifetime = int(c.raw.get("budget", {}).get("unseal_lifetime", 3 * c.unseal_limit))
    n = ledger.record_unseal(c.gym, freeze_hash, c.unseal_limit, {"status": "started", "seed": seed}, sealed_set, lifetime)   # spend first
    try:
        out = Path(workdir) / "sealed" / f"{freeze_hash[:12]}_{n}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "spec.json").write_text(json.dumps(f["candidate"], sort_keys=True))
        mp = {"python": python or sys.executable, "stage": "final", "spec": str(out / "spec.json"),
              "out": str(out), "seed": str(seed), "attempt": "1"}
        res = run_command([a.format(**mp) for a in c.commands["final"]], out, c.trial_timeout_s * 4, [c.primary],
                          env_extra={"ANTS_ALLOWED_ROLES": SEALED_ROLES})
    except BaseException as e:                              # the credit is gone: close the look so the books balance
        ledger.record_sealed_result(c.gym, freeze_hash, n, None, f"gate error: {e!r}", f["evidence"])
        raise
    ledger.record_sealed_result(c.gym, freeze_hash, n, res.metrics, res.error, f["evidence"])
    if anchor_file is not None:
        ledger.write_anchor(anchor_file)
    if res.metrics is None:
        raise GateError(f"sealed evaluation failed (credit spent): {res.error}")
    return res.metrics

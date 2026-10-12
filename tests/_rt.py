"""Helpers shared by the registry tests: drive a model up the ladder through the real entry points (confirm, challenge, seal)."""
import numpy as np

from antslab.registry import Registry

FAMS = ("ETT", "weather", "ecl")


def window_ids(fam, n=300):
    return [(fam, "val", 96, i) for i in range(n)]


def base_losses(seed=0, fams=FAMS, n=300):
    rng = np.random.default_rng(seed)
    return {f: rng.gamma(2, 1, n) for f in fams}


def record(reg, model_id, losses, factor=1.0, epoch="", ids=None, mae_factor=None):
    """Loss record of a model whose per-window loss is `factor` x the shared base losses."""
    ids = ids or {f: window_ids(f, len(v)) for f, v in losses.items()}
    L = {f: v * factor for f, v in losses.items()}
    mae = {f: np.sqrt(v) * (factor if mae_factor is None else mae_factor) ** 0.5 for f, v in losses.items()}
    return Registry.loss_record(model_id, L, ids, mae=mae, epoch=epoch)


def confirm(reg, mid, rel_mse=0.9, n_params=90):
    m = {"rel_mse": rel_mse, "n_params": n_params}
    return reg.confirm(mid, m, dict(m))


def validate(reg, mid, factor, base, inc_factor, epoch=""):
    """confirm + challenge the incumbent of the bin; returns the challenge verdict dict."""
    m = reg.get(mid)
    assert confirm(reg, mid)["passed"]
    inc = reg.incumbent(m["gym"], m["cap_bin"])
    return reg.challenge(mid, record(reg, mid, base, factor, epoch), record(reg, inc["id"], base, inc_factor, epoch))


def fake_sealed(ledger, gym, spec, fp="code0", epoch=None, sealed_set="", limit=4):
    """freeze -> spend a look -> record the result, exactly the order gate.seal uses (without running a gym)."""
    fh = ledger.freeze(gym, spec, fp, {"note": "test"}, epoch=epoch)
    n = ledger.record_unseal(gym, fh, limit, {"status": "started"}, sealed_set, None)
    ledger.record_sealed_result(gym, fh, n, {"val_mse": 0.5}, None, {"note": "test"})
    return fh


def climb(reg, ledger, mid, factor, base, inc_factor):
    """candidate -> champion through confirm, challenge and a sealed result."""
    d = validate(reg, mid, factor, base, inc_factor)
    assert d["verdict"] == "accepted", d
    fh = fake_sealed(ledger, reg.get(mid)["gym"], reg.get(mid)["spec"])
    reg.transition(mid, "champion", {"freeze_hash": fh})
    return fh

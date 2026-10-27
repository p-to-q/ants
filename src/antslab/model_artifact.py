"""Explicit train-only TinyTS refits and portable, parity-checked weights.

No pickle, automatic promotion, historical-checkpoint claim or evaluator edits.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

import numpy as np

from .candidates import _read, read_record
from .gyms.tinyts import Model, g
from .ledger import canon

MAX_WEIGHTS_BYTES = 64 << 20


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Predictor:
    def __init__(self, models, mean, scale):
        self.models, self.mean, self.scale = models, mean, scale

    def predict(self, raw_windows, channels=None):
        x = np.asarray(raw_windows, dtype=np.float64)
        if x.ndim != 2 or not np.isfinite(x).all():
            raise ValueError("prediction input must be a finite rows-by-lookback matrix")
        if x.shape[1] < max(m.L for m in self.models):
            raise ValueError("prediction input is shorter than the model lookback")
        if channels is None:
            channels = np.zeros(len(x), dtype=int) if len(self.mean) == 1 else np.arange(len(x))
        ids = np.asarray(channels)
        if ids.shape != (len(x),) or ids.dtype.kind not in "iu" or np.any(ids < 0) or np.any(ids >= len(self.mean)):
            raise ValueError("valid channel ids are required for each prediction row")
        z = (x - self.mean[ids, None]) / self.scale[ids, None]
        if g(self.models[0].sp, "clip"):
            z = np.clip(z, -g(self.models[0].sp, "clip_z"), g(self.models[0].sp, "clip_z"))
        z = z.astype(np.float32)
        y = sum(m.predict(z) for m in self.models) / len(self.models)
        return y * self.scale[ids, None] + self.mean[ids, None]


def load(path):
    """Verify bytes and load arrays into the installed reference implementation."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("artifact must not be a symlink")
    meta = _read(path / "manifest.json")
    if meta.get("schema") != 1 or meta.get("origin") != "refitted_train_only":
        raise ValueError("unsupported model artifact")
    files = meta.get("files", {})
    if not isinstance(files, dict) or "weights.npz" not in files or len(files) > 1000:
        raise ValueError("invalid artifact file inventory")
    for rel, expected in files.items():
        p = Path(rel)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError("unsafe artifact path")
        target = path / p
        if target.is_symlink() or any(parent.is_symlink() for parent in target.parents if parent != path.parent):
            raise ValueError("artifact files must not be symlinks")
        if not target.is_file() or target.stat().st_size > MAX_WEIGHTS_BYTES or _hash(target) != expected:
            raise ValueError(f"artifact checksum mismatch: {rel}")
    weights = path / "weights.npz"
    with zipfile.ZipFile(weights) as archive:
        if len(archive.infolist()) > 64 or sum(i.file_size for i in archive.infolist()) > MAX_WEIGHTS_BYTES:
            raise ValueError("weight archive exceeds size limit")
    models = []
    with np.load(weights, allow_pickle=False) as arrays:
        if not 1 <= len(meta.get("members", [])) <= 3:
            raise ValueError("invalid model member count")
        for i, member in enumerate(meta["members"]):
            h = member["horizon"]
            if not isinstance(h, int) or not 1 <= h <= 720:
                raise ValueError("invalid prediction horizon")
            model = Model(member["spec"])
            model.H = h
            model.core.H = h  # Fourier core also retains its fitted horizon
            for name in member["core_arrays"]:
                model.core.mats[name] = arrays[f"m{i}_core_{name}"].copy()
            for name in member["trend_arrays"]:
                model.mats_t[name] = arrays[f"m{i}_trend_{name}"].copy()
            models.append(model)
        mean, scale = arrays["mean"].copy(), arrays["scale"].copy()
        if mean.ndim != 1 or scale.shape != mean.shape or not len(mean) or not np.isfinite(mean).all() or not np.isfinite(scale).all() or np.any(scale <= 0):
            raise ValueError("invalid preprocessing state")
        count = sum(arrays[key].size for key in arrays)
        if count != meta["stored_numeric_values"] or count > meta["cap"]:
            raise ValueError("stored artifact exceeds its declared cap")
    return Predictor(models, mean, scale)


def save_refit(models, mean, scale, probe, out, cap, provenance):
    """Persist fitted weights and preprocessing, verify reload predictions, publish once."""
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise FileExistsError("artifact destination already exists; use a new version")
    arrays = {"mean": np.asarray(mean), "scale": np.asarray(scale)}
    members = []
    for i, model in enumerate(models):
        for name, value in model.core.mats.items():
            arrays[f"m{i}_core_{name}"] = value
        for name, value in model.mats_t.items():
            arrays[f"m{i}_trend_{name}"] = value
        members.append({"spec": model.sp, "horizon": model.H,
                        "core_arrays": list(model.core.mats), "trend_arrays": list(model.mats_t)})
    count = sum(a.size for a in arrays.values())
    if not isinstance(cap, (int, float)) or isinstance(cap, bool) or not np.isfinite(cap) or count > cap:
        raise ValueError(f"actual dense weights plus preprocessing ({count}) exceed cap {cap}")
    if any(a.dtype.kind not in "fi" or not np.isfinite(a).all() for a in arrays.values()):
        raise ValueError("model arrays must be finite numeric arrays")
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".refit-", dir=out.parent))
    try:
        np.savez_compressed(temp / "weights.npz", **arrays)
        source = Path(__file__).parent
        for p in source.rglob("*.py"):
            if p.is_symlink():
                raise ValueError("source snapshot must not contain symlinks")
            target = temp / "source/antslab" / p.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
        (temp / "README.md").write_text(
            "# Development refit\n\nThis is a newly fitted model on declared development training data, not the historical trial checkpoint.\n"
            "No confirm/sealed result or publication readiness is implied.\n\n"
            "Load with `antslab.model_artifact.load(PATH)` and call `.predict(raw_windows, channels)`; rows are channels/series and columns are lookback samples.\n"
            "The source snapshot is under source/; set PYTHONPATH to that folder. Install the NumPy version recorded in manifest.json in an isolated environment.\n"
            "All stored dense weights and preprocessing scalars count toward the cap. Quantised/pruned values remain dense numeric arrays; this is not an int8 or sparse runtime.\n")
        files = {p.relative_to(temp).as_posix(): _hash(p) for p in temp.rglob("*") if p.is_file()}
        meta = {"schema": 1, "origin": "refitted_train_only", "status": "development_refit",
                "release_ready": False, "members": members, "cap": cap,
                "stored_numeric_values": count, "numpy_version": np.__version__,
                "python_version": sys.version.split()[0], "provenance": provenance,
                "files": files}
        (temp / "manifest.json").write_text(canon(meta) + "\n")
        expected = Predictor(models, np.asarray(mean), np.asarray(scale)).predict(probe)
        actual = load(temp).predict(probe)
        if not np.allclose(expected, actual, rtol=0, atol=1e-10):
            raise ValueError("saved model failed load-predict parity")
        meta["parity"] = {"rows": len(probe), "max_abs_error": float(np.max(np.abs(expected - actual))),
                          "absolute_tolerance": 1e-10, "probe_role": "training inputs only"}
        (temp / "manifest.json").write_text(canon(meta) + "\n")
        if out.exists():
            raise FileExistsError("artifact destination appeared during export")
        os.rename(temp, out)
        return {"path": str(out), "origin": meta["origin"], "stored_numeric_values": count,
                "parity": meta["parity"], "weights_sha256": files["weights.npz"]}
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def refit(store, record_id, contract_path, dataset, horizon, out):
    from .contract import load_contract
    from .series import compute_epoch
    from .gyms import tinyts as T
    from .data import sample_windows
    record = read_record(store, record_id)
    candidate = record["candidate"]
    c = load_contract(contract_path)
    if c.gym != "tinyts" or compute_epoch(str(contract_path)) != candidate["identity"]["epoch"]:
        raise ValueError("refit requires the candidate's TinyTS evaluator identity")
    if _hash(contract_path) != candidate["identity"]["contract_sha256"]:
        raise ValueError("refit contract differs from the recorded candidate")
    if candidate.get("cap") not in c.caps:
        raise ValueError("candidate cap is not declared in the contract")
    if horizon not in (96, 192, 336, 720):
        raise ValueError("refit horizon must be 96, 192, 336 or 720")
    cat = T.catalog()
    cat.require_roles([dataset], {"dev"})  # reject before loading any data
    ser = cat.load(dataset)
    train = ser.values[:ser.n_train]
    mean, scale = train.mean(0), train.std(0) + 1e-8
    if len(train) < 2 * horizon + 16:
        raise ValueError("training segment is too short")
    spec = c.space.validate(candidate["spec"])
    if T.count_params(spec) > candidate["cap"]:
        raise ValueError("candidate specification exceeds its reference-horizon cap")
    data = (train - mean) / scale
    if g(spec, "clip"):
        data = np.clip(data, -g(spec, "clip_z"), g(spec, "clip_z"))
    data = data.astype(np.float32)
    fitted, priors, recipe = [], {}, []
    for ms in T.members(spec):
        length = min(T.eff_len(ms["lookback"], ms["period"]), max(ms["period"], (len(train)-horizon)//3))
        ms = {**ms, "lookback": length}
        cap_tr = max(64, min(T.CAT_STAGES["s2"]["cap_tr"], T.MAX_WINDOW_ELEMS // (4 * (length+horizon))))
        x, y, tt = sample_windows(data, 0, len(train), length, horizon, cap_tr, 11, with_t=True)
        w = np.exp(g(ms,"recency")*(tt-1.)) if g(ms,"recency") else None
        aug = (np.random.default_rng(12345),g(ms,"aug_sigma")) if g(ms,"augment") else None
        prior = None
        if g(ms,"pretrain") == "synth" and ms["core"] in T.PRIOR_CORES:
            prior = T._prior_model(ms,horizon,4,T.PROFILES["hard"][1],priors)
        elif g(ms,"pretrain") == "pool" and ms["core"] in T.PRIOR_CORES:
            prior = T._pool_prior(ms,length,horizon,ser.family,cat)
        fitted.append(Model(ms).fit(x,y,w,prior,aug).post_train())
        recipe.append({"lookback":length,"training_windows":len(x),"sampling_seed":11})
    width = max(m.L for m in fitted)
    probe = train[-width:].T
    return save_refit(fitted,mean,scale,probe,out,candidate["cap"],
        {"candidate_id":record_id,"candidate_identity":candidate["identity"],"dataset":dataset,
         "role":"dev","training_rows":len(train),"horizon":horizon,"recipe":recipe,
         "target_catalog_protocol":cat.protocol_hash(),
         "training_data_sha256":hashlib.sha256(np.ascontiguousarray(train).tobytes()).hexdigest(),
         "note":"New train-only fit; validation/test targets were not used. Other dev training families may provide a declared pool prior."})

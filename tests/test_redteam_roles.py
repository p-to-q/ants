"""Red team: can sealed / confirm / stress data reach a stage that must not see it?"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from .data_required import needs_raw

from antslab.contract import SpecError, load_contract
from antslab.data import ROLES_ENV, Catalog, CatalogError, RoleError, role_policy
from antslab.gyms import tinyts as T

ROOT = Path(__file__).resolve().parents[1]
CAT = ROOT / "data" / "catalog.toml"
HAVE_DATA = (CAT.parent / "processed" / "electricity.npz").exists() or (CAT.parent / "raw" / "electricity").exists()
needs_data = pytest.mark.skipif(not HAVE_DATA, reason="datasets not fetched (scripts/fetch_data.sh)")


@pytest.fixture(scope="module")
def cat():
    return Catalog(CAT)


# ---- the attack: the evaluation API accepts any dataset name for any split ---------------------------------------
@needs_data
def test_evaluate_catalog_cannot_be_driven_with_sealed_confirm_or_surrogate_data_under_a_search_policy(cat):
    spec = T.baseline_specs()["period_linear"]
    kw = dict(split="val", horizons=[96], cap_tr=1500, cap_va=800)
    with role_policy(("dev",)):                                              # what a search-stage process runs under
        assert T.evaluate_catalog(spec, ["etth1"], **kw)["rel_mse"] > 0       # dev still works
        for name in ("electricity", "etth2", "syn_phase_randomised_electricity", "syn_phase_randomised_solar", "syn_white_noise_1"):
            with pytest.raises(RoleError):
                T.evaluate_catalog(spec, [name], **kw)
    with role_policy(("dev", "confirm")):                                    # E2 may use confirm data, but not sealed information
        with pytest.raises(RoleError):
            T.evaluate_catalog(spec, ["syn_phase_randomised_electricity"], **kw)


@needs_data
def test_the_final_command_is_refused_outside_gate_seal_when_a_search_policy_is_inherited(tmp_path):
    """`python -m antslab.gyms.tinyts --final` run by a search worker (ANTS_ALLOWED_ROLES set to a search policy) must die
    before touching confirm / sealed data."""
    import json
    (tmp_path / "spec.json").write_text(json.dumps(T.baseline_specs()["period_linear"]))
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), ROLES_ENV: "stress"}
    r = subprocess.run([sys.executable, "-m", "antslab.gyms.tinyts", "--profile", "catalog", "--final", "--spec",
                        str(tmp_path / "spec.json"), "--out", str(tmp_path)], env=env, capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode != 0 and "RoleError" in r.stderr and not (tmp_path / "metrics.json").exists()


def test_the_policy_variable_is_fail_closed(cat, monkeypatch):
    monkeypatch.setenv(ROLES_ENV, "")                                         # set but empty: deny everything
    with pytest.raises(RoleError):
        cat.load("etth1")
    monkeypatch.setenv(ROLES_ENV, "dev,nonsense")                             # garbled: deny everything
    with pytest.raises(RoleError):
        cat.load("etth1")
    monkeypatch.setenv(ROLES_ENV, "dev")
    with pytest.raises(RoleError):
        cat.load("no_such_dataset")                                           # unknown names are refused, not skipped


# ---- require_roles --------------------------------------------------------------------------------------------------
def test_require_roles_fails_closed(cat):
    assert cat.require_roles(["etth1", "ettm1"], ("dev",)) == ["etth1", "ettm1"]
    with pytest.raises(RoleError, match="electricity"):
        cat.require_roles(["etth1", "electricity"], ("dev",))
    with pytest.raises(RoleError, match="unknown dataset"):
        cat.require_roles(["typo"], ("dev",))
    with pytest.raises(CatalogError):
        cat.require_roles(["etth1"], ("dev", "admin"))


def test_every_search_stage_of_the_real_gym_uses_dev_data_only(cat):
    for stage, cfg in T.CAT_STAGES.items():
        cat.require_roles(cfg["names"], ("dev",))


# ---- surrogates inherit the role of their base -------------------------------------------------------------------------
def test_surrogates_of_sealed_and_confirm_data_inherit_the_stricter_role(cat):
    synreal = [n for n, m in cat.meta.items() if m.get("format") == "synth_real"]
    assert len(synreal) == 16
    for n in synreal:
        base_role = cat.meta[cat.meta[n]["synth_base"]]["role"]
        assert cat.meta[n]["role"] == "stress"                                # declared role is unchanged...
        assert cat.access_role(n) == (base_role if base_role in ("sealed", "confirm") else "stress"), n   # ...the access role is not
    assert cat.access_role("syn_ar_simulation_electricity") == "sealed"
    assert cat.access_role("syn_shuffled_solar") == "confirm"
    assert cat.access_role("syn_phase_randomised_etth1") == "stress"
    assert cat.access_role("syn_white_noise_1") == "stress"
    stress_ok = set(cat.names_for("stress"))
    assert not any(n.endswith("_electricity") and n.startswith("syn_") for n in stress_ok if cat.meta[n].get("format") == "synth_real")
    assert set(cat.names_for("sealed")) >= set(cat.names("sealed")) | {"syn_phase_randomised_electricity"}


@needs_raw
def test_surrogates_remain_loadable_for_stress_diagnostics(cat):
    with role_policy(("stress",)):
        assert cat.load("syn_phase_randomised_etth1").values.shape[1] <= 8   # the base is read internally, not through the policy


# ---- the catalog cannot be silently edited into a different protocol ---------------------------------------------------
def _mini(tmp_path, role="dev", extra=""):
    csv = "date,a,b\n" + "\n".join(f"{i},{i % 7},{(i * 3) % 11}" for i in range(400)) + "\n"
    (tmp_path / "raw").mkdir(exist_ok=True)
    (tmp_path / "raw" / "x.csv").write_text(csv)
    (tmp_path / "catalog.toml").write_text(textwrap.dedent(f"""
        [dataset.rt_x]
        family = "F"
        role = "{role}"
        raw = "raw/x.csv"
        format = "ett_csv"
        split = [0.6, 0.2, 0.2]
        ref_period = 7
        horizons = [8]
    """))
    if extra:
        (tmp_path / "catalog_synth.toml").write_text(extra)
    return tmp_path / "catalog.toml"


def test_an_addon_file_cannot_redefine_a_protocol_dataset(tmp_path):
    p = _mini(tmp_path, "sealed", extra='[dataset.rt_x]\nfamily="F"\nrole="stress"\nraw="x"\nformat="synth"\n')
    with pytest.raises(CatalogError, match="redefines"):
        Catalog(p)


def test_an_addon_file_may_only_add_stress_datasets(tmp_path):
    p = _mini(tmp_path, extra='[dataset.rt_new]\nfamily="G"\nrole="dev"\nraw="x"\nformat="synth"\n')
    with pytest.raises(CatalogError, match="stress"):
        Catalog(p)


def test_unknown_roles_and_dangling_bases_are_refused(tmp_path):
    with pytest.raises(CatalogError, match="role"):
        Catalog(_mini(tmp_path, "godmode"))
    (tmp_path / "b").mkdir()
    p = _mini(tmp_path / "b",
              extra='[dataset.rt_s]\nfamily="G"\nrole="stress"\nraw="x"\nformat="synth_real"\nsynth_base="ghost"\n')
    with pytest.raises(CatalogError, match="ghost"):
        Catalog(p)


def test_processed_files_cannot_override_the_role_in_the_catalog(tmp_path):
    p = _mini(tmp_path, "dev")
    cat = Catalog(p)
    cat.build_processed(["rt_x"])                                             # bakes role=dev into data/processed/rt_x.npz
    from antslab import data
    data._CACHE.pop("rt_x", None)
    assert cat.load("rt_x").role == "dev"
    p.write_text(p.read_text().replace('role = "dev"', 'role = "sealed"'))    # the protocol changes; processed/ is now stale
    data._CACHE.pop("rt_x", None)
    cat2 = Catalog(p)
    assert cat2.load("rt_x").role == "sealed"
    with role_policy(("dev",)):
        with pytest.raises(RoleError):
            cat2.load("rt_x")


def test_protocol_hash_moves_with_roles_splits_and_the_manifest(tmp_path):
    p = _mini(tmp_path, "dev")
    h0 = Catalog(p).protocol_hash()
    assert Catalog(p).protocol_hash() == h0
    p.write_text(p.read_text().replace('role = "dev"', 'role = "confirm"'))
    h1 = Catalog(p).protocol_hash()
    assert h1 != h0
    (tmp_path / "MANIFEST.sha256").write_text("0" * 64 + "  raw/x.csv\n")
    assert Catalog(p).protocol_hash() != h1


# ---- other channels -----------------------------------------------------------------------------------------------
def test_a_spec_or_a_hint_has_no_way_to_name_a_dataset():
    c = load_contract(ROOT / "contracts" / "tinyts_real.toml")
    assert not {"names", "dataset", "datasets", "role", "split", "stage"} & set(c.space.params)
    spec = c.space.defaults()
    c.space.validate(spec)
    for key in ("names", "dataset", "split"):
        with pytest.raises(SpecError, match="unknown keys"):
            c.space.validate({**spec, key: ["electricity"]})                  # LLM output enters only through this door


@needs_data
def test_pooled_pretraining_draws_only_from_dev_data_of_other_families(cat, monkeypatch):
    loaded = []
    real = cat.load
    monkeypatch.setattr(cat, "load", lambda n: (loaded.append(n), real(n))[1])
    spec = {**T.baseline_specs()["period_linear"], "core": "linear", "period": 1, "lookback": 48, "pretrain": "pool"}
    for target in ("ETT", "ECL", "Weather"):                                 # dev, sealed and dev target families
        T._POOL_CACHE.clear(); loaded.clear()
        T._pool_prior(spec, 48, 24, target, cat, cap=200)
        assert loaded, target
        assert {cat.meta[n]["role"] for n in loaded} == {"dev"}
        assert target not in {cat.meta[n]["family"] for n in loaded}

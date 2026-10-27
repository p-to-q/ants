import json

import numpy as np
import pytest

from antslab import model_artifact as A
from antslab.gyms.tinyts import Model, baseline_specs


def fitted(core):
    rng=np.random.default_rng(31)
    spec={**baseline_specs()["period_linear"],"lookback":48,"core":core,
          "fourier_k":4,"rank":4,"decomp":"ma","trend_stride":24}
    x=rng.normal(size=(80,48)).astype(np.float32)
    y=rng.normal(size=(80,12)).astype(np.float32)
    return Model(spec).fit(x,y).post_train(),x[:4]


@pytest.mark.parametrize("core",["linear","period","fourier","basis"])
def test_all_core_weights_and_preprocessing_roundtrip(core,tmp_path):
    model,probe=fitted(core)
    out=tmp_path/"model"
    result=A.save_refit([model],np.array([.7]),np.array([1.4]),probe,out,10000,{"test":True})
    assert result["parity"]["max_abs_error"] == 0
    assert (out/"source/antslab/gyms/tinyts.py").is_file()
    meta=json.loads((out/"manifest.json").read_text())
    assert meta["origin"]=="refitted_train_only" and meta["release_ready"] is False
    before=A.Predictor([model],np.array([.7]),np.array([1.4])).predict(probe)
    assert np.array_equal(before,A.load(out).predict(probe))
    with pytest.raises(FileExistsError):
        A.save_refit([model],np.array([.7]),np.array([1.4]),probe,out,10000,{})


def test_cap_counts_preprocessing_and_all_dense_weights(tmp_path):
    model,probe=fitted("period")
    dense=sum(d[k].size for d,k in model._all_mats())
    with pytest.raises(ValueError,match="preprocessing"):
        A.save_refit([model],np.zeros(1),np.ones(1),probe,tmp_path/"no",dense,{})
    assert not (tmp_path/"no").exists()


def test_corrupt_weights_are_not_loaded(tmp_path):
    model,probe=fitted("period")
    out=tmp_path/"model"
    A.save_refit([model],np.zeros(1),np.ones(1),probe,out,10000,{})
    with (out/"weights.npz").open("ab") as f:
        f.write(b"corrupt")
    with pytest.raises(ValueError,match="checksum"):
        A.load(out)


def test_parent_directory_alias_is_allowed_but_bundle_alias_is_not(tmp_path):
    model,probe=fitted("period")
    actual=tmp_path/"actual";actual.mkdir()
    alias=tmp_path/"alias";alias.symlink_to(actual,target_is_directory=True)
    A.save_refit([model],np.zeros(1),np.ones(1),probe,alias/"model",10000,{})
    assert A.load(alias/"model").predict(probe).shape==(4,12)
    bundle_link=tmp_path/"bundle-link";bundle_link.symlink_to(actual/"model",target_is_directory=True)
    with pytest.raises(ValueError,match="symlink"):
        A.load(bundle_link)


def test_prediction_requires_explicit_valid_channel_ids():
    model,probe=fitted("period")
    pred=A.Predictor([model],np.array([0.,1.]),np.array([1.,2.]))
    with pytest.raises(ValueError,match="channel"):
        pred.predict(probe)
    assert pred.predict(probe,channels=[0,1,0,1]).shape==(4,12)


def test_refit_refuses_non_development_data_before_loading(monkeypatch,tmp_path):
    from antslab import candidates,series,contract
    from antslab.gyms import tinyts
    from types import SimpleNamespace
    path=tmp_path/"contract.toml";path.write_text("fake")
    candidate={"identity":{"epoch":"e","contract_sha256":A._hash(path)},"cap":1000}
    monkeypatch.setattr(A,"read_record",lambda *a:{"candidate":candidate})
    monkeypatch.setattr(contract,"load_contract",lambda *a:SimpleNamespace(gym="tinyts",caps=[1000]))
    monkeypatch.setattr(series,"compute_epoch",lambda *a:"e")
    class Cat:
        def require_roles(self,names,allowed):
            assert names==["sealed"] and allowed=={"dev"}
            raise ValueError("forbidden role")
        def load(self,*a):
            pytest.fail("must refuse before loading")
    monkeypatch.setattr(tinyts,"catalog",lambda:Cat())
    with pytest.raises(ValueError,match="role"):
        A.refit("unused","id",path,"sealed",96,tmp_path/"out")


def test_train_only_refit_exports_real_weights_without_using_future_targets(monkeypatch,tmp_path):
    from pathlib import Path
    from antslab.data import Series
    from antslab.series import compute_epoch
    from antslab.gyms import tinyts
    path=Path(__file__).resolve().parents[1]/"contracts/tinyts_real.toml"
    candidate={"identity":{"epoch":compute_epoch(path),"contract_sha256":A._hash(path)},
               "spec":baseline_specs()["period_linear"],"cap":1000}
    monkeypatch.setattr(A,"read_record",lambda *a:{"candidate":candidate})
    train=np.sin(np.arange(600)/24.)[:,None]
    values=np.concatenate([train,np.full((200,1),1e9)])
    class Cat:
        def require_roles(self,names,allowed):
            assert names==["demo"] and allowed=={"dev"}
        def load(self,name):
            return Series("demo","demo","dev",values,600,100,24,[96])
        def protocol_hash(self):
            return "synthetic-test-catalog"
    monkeypatch.setattr(tinyts,"catalog",lambda:Cat())
    result=A.refit("unused","id",path,"demo",96,tmp_path/"out")
    model=A.load(tmp_path/"out")
    assert model.mean[0] == pytest.approx(train.mean())
    assert result["origin"]=="refitted_train_only" and result["parity"]["max_abs_error"]==0
    assert model.predict(train[-336:].T).shape==(1,96)

import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest

from antslab import model_validation as V, model_artifact as A
from antslab.data import Series
from antslab.gyms import tinyts


def test_validation_uses_only_dev_val_targets_and_keeps_bundle_unchanged(tmp_path,monkeypatch):
    bundle=tmp_path/'model';bundle.mkdir();(bundle/'manifest.json').write_text('{}');(bundle/'weights.npz').write_bytes(b'weights')
    train=np.sin(np.arange(400)/24.)[:,None];val=np.sin(np.arange(400,592)/24.)[:,None]
    values=np.concatenate([train,val,np.full((200,1),1e9)])
    provenance={'dataset':'demo','horizon':96,'training_data_sha256':hashlib.sha256(np.ascontiguousarray(train).tobytes()).hexdigest()}
    class Cat:
        def require_roles(self,names,allowed):assert names==['demo'] and allowed=={'dev'}
        def load(self,name):return Series('demo','demo','dev',values,400,192,24,[96])
    predictor=SimpleNamespace(models=[SimpleNamespace(L=48,H=96)],scale=np.array([train.std()+1e-8]),predict=lambda x,ids:np.tile(x[:,-24:],(1,4)))
    monkeypatch.setattr(A,'load',lambda path:predictor);monkeypatch.setattr(A,'_read',lambda path:{'provenance':provenance})
    monkeypatch.setattr(tinyts,'catalog',lambda:Cat())
    report=V.evaluate(bundle,tmp_path/'quality.json')
    assert report['protocol']['target_bounds']==[400,592]
    assert report['protocol']['evaluated_windows']==2
    assert report['metrics']['rel_mse']==pytest.approx(1)
    assert max(report['trace']['actual'])<2 and report['trace']['target_start']>=400
    assert (bundle/'manifest.json').read_text()=='{}'
    with pytest.raises(FileExistsError):V.evaluate(bundle,tmp_path/'quality.json')


def test_nondev_role_is_refused_before_data_loading(tmp_path,monkeypatch):
    bundle=tmp_path/'model';bundle.mkdir()
    predictor=SimpleNamespace(models=[SimpleNamespace(H=96)])
    monkeypatch.setattr(A,'load',lambda path:predictor)
    monkeypatch.setattr(A,'_read',lambda path:{'provenance':{'dataset':'sealed','horizon':96}})
    class Cat:
        def require_roles(self,*a):raise ValueError('forbidden role')
        def load(self,*a):pytest.fail('loaded sealed data')
    monkeypatch.setattr(tinyts,'catalog',lambda:Cat())
    with pytest.raises(ValueError,match='role'):V.evaluate(bundle,tmp_path/'out')
    assert not (tmp_path/'out').exists()

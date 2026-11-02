import json
import numpy as np
from antslab.gyms.tiny_nn import evaluate


def test_chronological_checkpoint_and_determinism(tmp_path):
    spec={'kind':'mlp','width':8}
    a=evaluate(spec,'s2',tmp_path/'a',17);b=evaluate(spec,'s2',tmp_path/'b',17)
    assert a==b and a['n_params']==179 and a['n_windows']==16
    receipt=json.loads((tmp_path/'a/checkpoint.json').read_text())
    assert receipt['train_bounds'][1]<=min(receipt['origins'])
    assert max(receipt['origins'])+16<=receipt['validation_bounds'][1]
    assert receipt['test_accessed'] is False
    with np.load(tmp_path/'a/checkpoint.npz',allow_pickle=False) as z:
        assert sum(z[k].size for k in z)==a['n_params']


def test_ridge_and_cheap_stages_do_not_export_checkpoints(tmp_path):
    r=evaluate({'kind':'ridge','width':8},'s0',tmp_path/'ridge')
    assert r['n_params']==51 and not (tmp_path/'ridge/checkpoint.npz').exists()


def test_checkpoint_tamper_is_rejected_and_attempt_is_immutable(tmp_path):
    import pytest
    from antslab.gyms.tiny_nn import forecast
    evaluate({'kind':'ridge','width':4},'s2',tmp_path/'r')
    with pytest.raises(FileExistsError):evaluate({'kind':'ridge','width':4},'s2',tmp_path/'r')
    file=tmp_path/'r/checkpoint.npz';file.write_bytes(file.read_bytes()+b'tamper')
    with pytest.raises(ValueError,match='checksum'):forecast(tmp_path/'r',np.zeros(16))

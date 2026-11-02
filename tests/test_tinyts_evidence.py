import json
import numpy as np
from antslab.gyms import tinyts as T
from antslab.gyms.tinyts_evidence import evaluate
from antslab.data import Series
from antslab.checkpoint_store import verify_inventory


def test_instrumentation_preserves_score_and_captures_real_state(tmp_path,monkeypatch):
    rng=np.random.default_rng(4);values=rng.normal(size=(900,2)).cumsum(0)
    class Cat:
        def load(self,name):return Series('demo','demo','dev',values,600,200,24,[16])
        def require_roles(self,names,roles):assert roles=={'dev'}
    monkeypatch.setattr(T,'catalog',lambda:Cat())
    monkeypatch.setitem(T.CAT_STAGES,'s2',dict(names=['demo'],H=[16],cap_tr=64,cap_va=32,folds=1))
    spec={**T.baseline_specs()['period_linear'],'lookback':48,'period':24}
    baseline=T.evaluate_stage_catalog(spec,'s2');out=tmp_path/'capture';result=evaluate(spec,'s2',out)
    assert all(result[k]==v for k,v in baseline.items() if k!='fit_s')
    coverage=json.loads((out/'coverage.json').read_text())
    task=coverage['tasks'][0]
    assert task['checkpoint']=='saved' and task['windows']==32
    assert task['scoring_max_abs_error']<1e-5
    assert task['train_end']==600 and task['validation_bounds']==[600,800]
    sha,paths=verify_inventory(out)
    assert any(p.name=='weights.npz' for p in paths) and sha


def test_strict_fold_normalisation_cannot_see_later_values(tmp_path,monkeypatch):
    rng=np.random.default_rng(9);values=rng.normal(size=(900,2)).cumsum(0)
    class Cat:
        def load(self,name):return Series('demo','demo','dev',values.copy(),600,200,24,[16])
        def require_roles(self,names,roles):assert roles=={'dev'}
    monkeypatch.setattr(T,'catalog',lambda:Cat())
    monkeypatch.setitem(T.CAT_STAGES,'s2',dict(names=['demo'],H=[16],cap_tr=64,cap_va=32,folds=3))
    spec={**T.baseline_specs()['period_linear'],'lookback':48,'period':24}
    evaluate(spec,'s2',tmp_path/'before',strict_folds=True)
    values[480:]+=1000  # after the first fold's fit boundary
    evaluate(spec,'s2',tmp_path/'after',strict_folds=True)
    with np.load(tmp_path/'before/checkpoints/task_000/weights.npz',allow_pickle=False) as a, np.load(tmp_path/'after/checkpoints/task_000/weights.npz',allow_pickle=False) as b:
        assert all(np.array_equal(a[k],b[k]) for k in a)
    coverage=json.loads((tmp_path/'after/coverage.json').read_text())
    assert all(t['normalisation_train_end']==t['train_end']<=t['validation_bounds'][0] for t in coverage['tasks'])

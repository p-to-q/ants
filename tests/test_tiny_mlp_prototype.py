"""Gradient and artifact checks for the isolated prototype, not benchmark claims."""
import importlib.util
from pathlib import Path
import numpy as np

spec = importlib.util.spec_from_file_location('tiny_mlp', Path(__file__).parents[1]/'scripts/prototype_tiny_mlp.py')
mlp = importlib.util.module_from_spec(spec); spec.loader.exec_module(mlp)


def test_backprop_matches_finite_differences():
    rng=np.random.default_rng(7);x=rng.normal(size=(9,16));y=rng.normal(size=(9,1));w=mlp.init()
    _,g=mlp.loss_grad(w,x,y)
    for key,idx in [('w1',(3,2)),('b1',(2,)),('w2',(3,0)),('b2',(0,))]:
        old=w[key][idx];eps=1e-6
        w[key][idx]=old+eps;plus=mlp.loss_grad(w,x,y)[0]
        w[key][idx]=old-eps;minus=mlp.loss_grad(w,x,y)[0];w[key][idx]=old
        assert np.isclose(g[key][idx],(plus-minus)/(2*eps),rtol=1e-5,atol=1e-7)


def test_training_checkpoint_reload_and_physical_cap(tmp_path):
    result=mlp.run(tmp_path/'model')
    assert result['curve'][-1]['train_mse'] < result['curve'][0]['train_mse']*.25
    assert result['stored_numeric_values']==177 and result['parity']=='exact'
    assert result['release_ready'] is False
    import pytest
    with pytest.raises(FileExistsError): mlp.run(tmp_path/'model')

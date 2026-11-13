import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('e44c',Path(__file__).parents[1]/'scripts/analyze_e44c.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_exact_signed_rank_zero_ties_and_extreme():
    assert module.signed_rank_p([0]*8)==1
    assert module.signed_rank_p([-1]*8)==2/256
    assert module.signed_rank_p([-1,1]*4)==1


def test_unregistered_design_is_refused():
    with pytest.raises(ValueError):module.analyze('/missing',{'seeds_parsed':[1],'max_cpu_s':7200,'arm_cpu_s':{}})


def test_preregistration_identity_and_retrospective_guard(tmp_path, monkeypatch):
    registration_spec=importlib.util.spec_from_file_location('register_e44c',Path(__file__).parents[1]/'scripts/register_e44c.py')
    reg=importlib.util.module_from_spec(registration_spec);registration_spec.loader.exec_module(reg)
    series=reg.load_series('deploy/series/e44c.toml')
    monkeypatch.setattr(reg,'load_series',lambda _:series)
    source=tmp_path/'source.tar.gz';source.write_bytes(b'public source fixture')
    out=tmp_path/'series'/'registration.json'
    document=reg.register('deploy/series/e44c.toml',source,'a'*40,out)
    assert document['arm_cpu_s']=={'portfolio':7200,'tpe_reference_v2':7200,'random':14400}
    assert reg.register('deploy/series/e44c.toml',source,'a'*40,out)==document
    with pytest.raises(ValueError,match='conflict'):
        reg.register('deploy/series/e44c.toml',source,'b'*40,out)
    wd=out.parent/'portfolio_s301';wd.mkdir();(wd/'ledger.sqlite').touch()
    with pytest.raises(ValueError,match='retrospectively'):
        reg.register('deploy/series/e44c.toml',source,'a'*40,out)
    series['arms']=['random']
    with pytest.raises(ValueError,match='unregistered'):
        reg.register('deploy/series/e44c.toml',source,'a'*40,out)

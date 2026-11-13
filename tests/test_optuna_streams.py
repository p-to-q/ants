import pytest
pytest.importorskip('optuna')
from antslab.optuna_reference_v2 import sampler_seed, OptunaReferenceV2
from antslab.optuna_reference import OptunaReference
from antslab.history import History


def test_adjacent_runs_do_not_reuse_shifted_seed_streams():
    streams=[{sampler_seed(seed,i) for i in range(1000)} for seed in range(301,309)]
    assert len(set.union(*streams)) == 8000
    assert sampler_seed(301,0)==sampler_seed(301,0)
    assert sampler_seed(301,1)!=sampler_seed(302,0)


def test_durable_recovery_and_distinct_adjacent_proposals(make_contract,ledger):
    c=make_contract()
    h=History(ledger,c,'tpe_reference_v2',epoch='stream-v2')
    p=OptunaReferenceV2(c,301)
    a=p.propose(ledger,'tpe_reference_v2',8,set(),hist=h)
    b=OptunaReferenceV2(c,301).propose(ledger,'tpe_reference_v2',8,set(),hist=h)
    assert [v.spec for v in a]==[v.spec for v in b]
    # Direct suggestion streams isolate the RNG from finite-space duplicate rejection.
    def stream(seed):
        adapter=OptunaReferenceV2(c,seed)
        study=adapter.o.create_study(sampler=adapter._sampler(0))
        result=[]
        for i in range(12):
            study.sampler=adapter._sampler(i)
            result.append(adapter._suggest(study.ask()))
        return result
    assert stream(301)[1:] != stream(302)[:-1]
    assert p.scope != OptunaReference(c,301).scope
    assert ledger.verify_chain()[0]

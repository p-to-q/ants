import pytest
pytest.importorskip('optuna')
from antslab.optuna_reference import OptunaReference
from antslab.history import History
from antslab.contract import Param


def test_conditional_canonical_and_crash_before_enqueue(make_contract,ledger):
    c=make_contract();c.space.params['depth']=Param('depth','int',low=1,high=4,when={'c':['u']})
    h=History(ledger,c,'tpe_reference',epoch='scope1')
    first=OptunaReference(c,17).propose(ledger,'tpe_reference',3,set(),hist=h)
    assert len(first)==3
    for p in first:
        if p.spec['c']!='u':assert p.spec['depth']==c.space.params['depth'].default_value()
    again=OptunaReference(c,17).propose(ledger,'tpe_reference',3,set(),hist=h)
    assert [p.spec for p in first]==[p.spec for p in again]
    assert len(ledger.events('optuna_intent'))==3
    assert ledger.verify_chain()[0]


def test_epoch_fidelity_pending_failure_and_no_duplicate_tell(make_contract,ledger):
    c=make_contract();arm='tpe_reference';h=History(ledger,c,arm,epoch='scope1');p=OptunaReference(c,19)
    first=p.propose(ledger,arm,2,set(),hist=h)
    for item in first:
        ledger.enqueue(c.gym,arm,1,item.spec,item.source,epoch='scope1',stage='s0',spec_key=c.space.key(item.spec))
    t=ledger.claim(60,arm=arm,epoch='scope1');ledger.finish(t['id'],t['attempt'],'failed')
    ledger.enqueue(c.gym,arm,1,first[0].spec,'other',epoch='old',stage='s0',spec_key='other')
    ledger.enqueue(c.gym,arm,1,first[0].spec,'other',epoch='scope1',stage='s1',spec_key='deep')
    seen=ledger.seen_keys(c.gym,arm,'scope1')
    result=OptunaReference(c,19).propose(ledger,arm,2,seen,hist=h)
    assert len(result)==2 and not ({c.space.key(x.spec) for x in first}&{c.space.key(x.spec) for x in result})
    telemetry=ledger.events('optuna_overhead')[-1]['payload']
    assert telemetry['terminal_used']==1 and telemetry['pending_used']==1
    assert telemetry['controller_cpu_s']>=0 and ledger.verify_chain()[0]


def test_dependencies_rejected_and_adapter_changes_only_new_arm_hash(make_contract):
    from antslab.proposers import make_proposer
    from antslab.campaign import Campaign
    c=make_contract()
    assert 'proposal_adapter' in Campaign.definition_of('tpe_reference',make_proposer('tpe_reference',c,1),c)
    assert 'proposal_adapter' not in Campaign.definition_of('random',make_proposer('random',c,1),c)
    c.space.params['a'].when={'missing':['u']}
    with pytest.raises(ValueError,match='dependencies'):OptunaReference(c)


def test_second_fidelity_uses_pending_first_stage_without_mixing_values(make_contract,ledger):
    c=make_contract();arm='tpe_reference';h=History(ledger,c,arm,epoch='s1epoch');p=OptunaReference(c,3,objective_stage='s1')
    item=p.propose(ledger,arm,1,set(),hist=h)[0]
    ledger.enqueue(c.gym,arm,1,item.spec,item.source,epoch='s1epoch',stage='s0',spec_key=c.space.key(item.spec))
    p.propose(ledger,arm,1,ledger.seen_keys(c.gym,arm,'s1epoch'),hist=h)
    event=ledger.events('optuna_overhead')[-1]['payload']
    assert event['terminal_used']==0 and event['pending_used']==1


def test_crash_after_durable_intent_reproduces_batch(make_contract,tmp_path):
    from antslab.ledger import Ledger
    c=make_contract();left=Ledger(tmp_path/'left.sqlite');right=Ledger(tmp_path/'right.sqlite');arm='tpe_reference'
    try:
        expected=OptunaReference(c,22).propose(left,arm,3,set(),hist=History(left,c,arm,epoch='e'))
        original=right.event
        def crash(kind,payload):
            original(kind,payload)
            if kind=='optuna_intent':raise RuntimeError('simulated crash after commit')
        right.event=crash
        with pytest.raises(RuntimeError):OptunaReference(c,22).propose(right,arm,3,set(),hist=History(right,c,arm,epoch='e'))
        right.event=original
        actual=OptunaReference(c,22).propose(right,arm,3,set(),hist=History(right,c,arm,epoch='e'))
        assert [p.spec for p in expected]==[p.spec for p in actual]
    finally:left.close();right.close()

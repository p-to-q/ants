import json
import pytest
from antslab.checkpoint_store import write_inventory,verify_inventory,register,digest


def test_registration_is_restart_safe_and_marker_last(tmp_path,ledger):
    out=tmp_path/'out';out.mkdir();(out/'checkpoint.json').write_text('{}');(out/'checkpoint.npz').write_bytes(b'weights')
    sha,_=write_inventory(out);original=ledger.add_artifact;calls=[]
    def crash(*args,**kwargs):
        value=original(*args,**kwargs);calls.append(1)
        if len(calls)==1:raise RuntimeError('crash after file registration')
        return value
    ledger.add_artifact=crash
    with pytest.raises(RuntimeError):register(ledger,1,out,sha)
    assert ledger.artifact(str(out/'checkpoint_inventory.json')) is None
    ledger.add_artifact=original;register(ledger,1,out,sha)
    assert len(ledger.artifacts())==3 and all(a['protected'] for a in ledger.artifacts())
    before=len(ledger.events('artifact_added'));register(ledger,1,out,sha)
    assert len(ledger.events('artifact_added'))==before and ledger.verify_chain()[0]
    (out/'checkpoint.npz').write_bytes(b'tampered')
    with pytest.raises(ValueError):verify_inventory(out,sha)


def test_inventory_rejects_traversal_and_symlinks(tmp_path):
    out=tmp_path/'out';out.mkdir();victim=tmp_path/'victim';victim.write_bytes(b'x')
    item={'path':'../victim','bytes':1,'sha256':digest(victim)}
    (out/'checkpoint_inventory.json').write_text(json.dumps({'schema':1,'files':[item]}))
    with pytest.raises(ValueError):verify_inventory(out)
    (out/'link').symlink_to(victim);item['path']='link'
    (out/'checkpoint_inventory.json').write_text(json.dumps({'schema':1,'files':[item]}))
    with pytest.raises(ValueError):verify_inventory(out)


def test_campaign_recovery_finishes_prepared_registration(tmp_path,ledger,make_contract):
    from antslab.campaign import Campaign
    from antslab.proposers import make_proposer
    c=make_contract();c.raw['record']={'checkpoints':True}
    camp=Campaign(ledger,c,tmp_path/'work','random',make_proposer('random',c,1))
    out=tmp_path/'attempt';out.mkdir();(out/'checkpoint.json').write_text('{}')
    sha,_=write_inventory(out)
    trial=ledger.enqueue(c.gym,'random',1,{'a':.2,'b':2,'c':'u'},'random',stage='s2',epoch=camp.epoch)
    t=ledger.claim(60,arm='random',epoch=camp.epoch)
    ledger.event('checkpoint_prepared',{'trial_id':trial,'attempt':t['attempt'],'epoch':camp.epoch,'sha256':sha})
    ledger.finish(trial,t['attempt'],'done',metrics={'val_mse':.1,'n_params':20},out_dir=str(out))
    camp.recover()
    assert ledger.artifact(str(out/'checkpoint_inventory.json'))['protected']==1
    assert ledger.verify_chain()[0]

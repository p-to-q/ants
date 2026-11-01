import json
from pathlib import Path
import shutil
import threading
import types

import pytest

from antslab import search_map as M
from antslab.ledger import Ledger
from antslab.series import compute_epoch
from antslab.tui import views,app,render


@pytest.fixture
def map_source(tmp_path,make_contract):
    c=make_contract();contract=tmp_path/'task.toml';epoch=compute_epoch(contract)
    wd=tmp_path/'source';l=Ledger(wd/'ledger.sqlite');l.register_epoch(epoch,{})
    l.event('daemon_started',{'arm':'random','epoch':epoch})
    last=None
    for i in range(4):
        spec={'a':.1+i*.1,'b':2,'c':'u'};key=c.space.key(spec)
        tid=l.enqueue(c.gym,'random',i,spec,'random',stage='s2',epoch=epoch,spec_key=key,origin_key=last)
        t=l.claim(60,arm='random',epoch=epoch)
        l.finish(tid,t['attempt'],'done',metrics={'val_mse':.9-i*.1,'n_params':50+i},cpu_s=2)
        last=key
    l.close();return c,contract,wd,epoch


def test_build_freezes_projection_is_readonly_and_censors_future(map_source,tmp_path):
    c,contract,wd,epoch=map_source
    before=(wd/'ledger.sqlite').read_bytes()
    out=tmp_path/'map.json'
    M.build_map(contract,[wd],out,budget=5,cap=100)
    m=M.read_map(out)
    assert len(m['points'])==2 and m['scope']['epoch']==epoch
    assert all(p['cpu_s']<=5 for p in m['points'])
    assert m['projection']['kind']=='fixed_retrospective_pca'
    assert (wd/'ledger.sqlite').read_bytes()==before
    with pytest.raises(FileExistsError):M.build_map(contract,[wd],out,budget=5,cap=100)
    for w,h in [(80,24),(100,30),(132,30)]:
        v=views.search_map_screen({'map':m},views.Ctx(width=w,height=h,sel=1))
        assert v.windowed and v.rows==2 and len(v.lines)<=h-3
        assert '@' in render.plain(v.lines) and 'retrospective' in render.plain(v.lines)


def test_duplicate_copies_and_mixed_epochs_fail_closed(map_source,tmp_path):
    _,contract,wd,_=map_source
    other=tmp_path/'copy';shutil.copytree(wd,other)
    with pytest.raises(ValueError,match='duplicate'):M.build_map(contract,[wd,other],tmp_path/'bad')
    changed=tmp_path/'changed.toml';changed.write_text(contract.read_text().replace('name="toy"','name="changed"'))
    with pytest.raises(ValueError,match='identity'):M.build_map(changed,[wd],tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_point_identity_survives_copy_and_head_append(map_source,tmp_path):
    _,contract,wd,_=map_source
    M.build_map(contract,[wd],tmp_path/'a')
    old=M.read_map(tmp_path/'a')
    l=Ledger(wd/'ledger.sqlite');l.event('user_hint',{'text':'test'});l.close()
    M.build_map(contract,[wd],tmp_path/'b')
    assert [p['id'] for p in old['points']]==[p['id'] for p in M.read_map(tmp_path/'b')['points']]
    assert old['sources'][0]['chain_head']!=M.read_map(tmp_path/'b')['sources'][0]['chain_head']


def test_bad_cache_and_future_values_are_unavailable(map_source,tmp_path):
    _,contract,wd,_=map_source
    out=tmp_path/'m';M.build_map(contract,[wd],out)
    x=json.loads(out.read_text());x['points'][0]['cpu_s']=999999
    out.write_text(json.dumps(x));assert M.read_map(out)['error']
    assert M.read_map(tmp_path/'missing')['missing']


def test_map_is_independent_of_live_source_and_mode_keys(map_source,tmp_path,monkeypatch):
    _,contract,wd,_=map_source
    out=tmp_path/'m';M.build_map(contract,[wd],out)
    monkeypatch.setenv('ANTS_SEARCH_MAP',str(out))
    a=app.App.__new__(app.App);a.current=lambda:pytest.fail('opened live source')
    d=a.load('fleet',{'fleet_mode':'map'},None)
    assert len(d['map']['points'])==4
    a.screen='fleet';a.ctx=views.Ctx();a.filt={};a.refresh=threading.Event();a.view=lambda:views.View([])
    import curses
    a.on_key(ord('m'),curses);assert a.filt['fleet_mode']=='map'
    a.on_key(ord('z'),curses);assert a.filt['map_height']=='score'


def test_windowed_views_do_not_scroll_twice():
    class Screen:
        lines=[]
        def getmaxyx(self):return 30,100
        def erase(self):self.lines=[]
        def addnstr(self,y,x,t,n,style):self.lines.append((y,t[:n]))
        def refresh(self):pass
    a=app.App.__new__(app.App);a.ctx=views.Ctx(sel=500);a.screen='fleet';a.scroll=0
    a.view=lambda:views.View([[('VISIBLE MAP','none')]],1000,3,True)
    a.refreshing=False;a.label='test';a.note='';a.note_until=0;a.data=app.Data()
    a.frozen=False;a.load_s=0
    s=Screen();a.draw(s,types.SimpleNamespace(error=RuntimeError),lambda _:0)
    assert (0,'VISIBLE MAP') in s.lines


def test_map_only_install_does_not_trigger_onboarding(tmp_path,monkeypatch):
    from antslab import cli
    from antslab.tui import model
    monkeypatch.setenv('ANTS_HOME',str(tmp_path/'home'))
    monkeypatch.delenv('ANTS_WORKDIR',raising=False);monkeypatch.delenv('ANTS_CONTRACT',raising=False)
    monkeypatch.setattr(model,'discover_workdirs',lambda:[])
    path=tmp_path/'map.json';path.write_text('{}')
    monkeypatch.setenv('ANTS_SEARCH_MAP',str(path))
    assert cli._first_run_done()

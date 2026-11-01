import copy
import json
from pathlib import Path
import threading

import pytest

from antslab.saved_models import read_catalog
from antslab.tui import app, views, render


@pytest.fixture
def catalog():
    return json.loads((Path(__file__).resolve().parents[1]/'docs/models/e43-shortlist-20261102.json').read_text())


def test_catalog_never_follows_bundle_paths_or_checks_weights(tmp_path,catalog,monkeypatch):
    path=tmp_path/'catalog.json'
    catalog['models'][0]['refit']['bundle']='/a/nonexistent/private/bundle'
    path.write_text(json.dumps(catalog))
    before=path.read_bytes(),path.stat().st_mtime_ns
    from antslab import model_artifact
    monkeypatch.setattr(model_artifact,'load',lambda *a:pytest.fail('viewer loaded weights'))
    got=read_catalog(path)
    assert len(got['models'])==10 and got['recorded_only']
    assert (path.read_bytes(),path.stat().st_mtime_ns)==before


@pytest.mark.parametrize('change', ['epoch','metric','nan','cap','duplicate','receipt_cap'])
def test_catalog_rejects_incompatible_or_invalid_evidence(tmp_path,catalog,change):
    row=catalog['models'][1];c=row['candidate']
    if change=='epoch':c['identity']['epoch']='another'
    if change=='metric':c['metric']='another'
    if change=='nan':c['value']=float('nan')
    if change=='cap':c['n_params']=c['cap']+1
    if change=='duplicate':c['spec_key']=catalog['models'][0]['candidate']['spec_key']
    if change=='receipt_cap':row['refit']['stored_numeric_values']=c['cap']+1
    path=tmp_path/'catalog.json';path.write_text(json.dumps(catalog))
    got=read_catalog(path)
    assert got['error'] and got['models']==[]


def test_missing_and_unreadable_are_distinct(tmp_path):
    assert read_catalog(tmp_path/'missing')['missing']
    path=tmp_path/'bad';path.write_text('{')
    assert 'error' in read_catalog(path) and 'missing' not in read_catalog(path)
    link=tmp_path/'link';link.symlink_to(path)
    assert 'error' in read_catalog(link)


def test_saved_view_loads_without_contract_ledger_or_training(tmp_path,catalog,monkeypatch):
    path=tmp_path/'catalog.json';path.write_text(json.dumps(catalog))
    monkeypatch.setenv('ANTS_MODEL_CATALOG',str(path))
    a=app.App.__new__(app.App)
    a.current=lambda:pytest.fail('opened live source')
    data=a.load('board',{'board_mode':'saved'},None)
    assert len(data['catalog']['models'])==10
    a.screen='board';a.ctx=views.Ctx(sel=4);a.filt={'board_mode':'saved'}
    a.data=app.Data();a.data.value=data
    a.goto=lambda screen:setattr(a,'screen',screen)
    a.enter(views.View([]))
    assert a.screen=='pager' and a.saved_selection==4
    assert 'Historical checkpoint' in a.pager_text
    assert catalog['models'][4]['candidate']['spec_key'] in a.pager_text


def test_saved_switch_does_not_modify_search():
    import curses
    a=app.App.__new__(app.App)
    a.screen='board';a.ctx=views.Ctx(sel=4,tab=2);a.filt={};a.ranks={1:1}
    a.refresh=threading.Event();a.view=lambda:views.View([])
    a.on_key(ord('v'),curses)
    assert a.filt['board_mode']=='saved' and a.ctx.sel==a.ctx.tab==0 and not a.ranks
    a.on_key(ord('v'),curses)
    assert a.filt['board_mode']=='live'


@pytest.mark.parametrize('width,height',[(72,24),(100,30),(120,30),(160,30)])
def test_saved_layout_scopes_scores_and_paginates_every_record(catalog,width,height):
    rows=catalog['models'];catalog['models']=[copy.deepcopy(rows[i%10]) for i in range(35)]
    ctx=views.Ctx(width=width,height=height,sel=34)
    v=views.saved_board({'catalog':catalog},ctx)
    text=render.plain(views.fit(v.lines,width))
    assert v.rows==35 and '/ 35' in text and 'Refit recorded' in text
    assert 'Refit dev MSE' in text and 'recorded receipts' in text
    assert ('│' in text)==(width>=120)
    assert len(v.lines)<=height-3


def test_recorded_views_do_not_pause_or_hint_a_live_search():
    import curses
    for screen,filt in [('board',{'board_mode':'saved'}),('fleet',{'fleet_mode':'map'}),('afterlife',{})]:
        a=app.App.__new__(app.App);a.ctx=views.Ctx();a.screen=screen;a.filt=filt;a.help=False
        a.view=lambda:views.View([])
        a.toggle_pause=lambda:pytest.fail('paused unrelated live search')
        a.prompt=lambda *a,**k:pytest.fail('hinted unrelated live search')
        a.say=lambda text:None
        a.on_key(ord('p'),curses);a.on_key(ord('h'),curses)


def test_validation_curve_is_never_fabricated_when_trace_not_bundled(catalog):
    d={'catalog':catalog}
    v=views.saved_forecast(d,views.Ctx(width=100,height=30))
    text=render.plain(v.lines)
    assert 'trace is not included' in text and 'Refit dev MSE' in text

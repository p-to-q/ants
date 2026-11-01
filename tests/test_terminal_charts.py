import math

from antslab.tui import charts,views,render


def test_scatter_keeps_explicit_units_missingness_and_fixed_bounds():
    text='\n'.join(charts.scatter([('failure','o',[(-5,0),(-3,100),(-1,float('nan'))])],72,5,
        xlim=(-6,0),ylim=(0,100),xlabel='hours before sample',ylabel='Failure (%)',ascii_only=True))
    assert 'Failure (%)' in text and 'hours before sample' in text
    assert '100' in text and '-6' in text and 'o' in text.splitlines()[1].split('|')[1]
    assert 'o' in text.splitlines()[5].split('|')[1]
    assert 'nan' not in text.lower() and text.count('o')>=2
    assert all(len(line)<=72 for line in text.splitlines())
    assert 'No observations' in '\n'.join(charts.scatter([('x','o',[(1,None)])]))


def test_catalog_counts_distinguish_present_missing_and_generated():
    roles={'dev':[('a',True),('b',False)],'sealed':[('c',False)],'synthetic':['s1','s2']}
    text='\n'.join(charts.role_bars(roles,80,True))
    assert '1/2' in text and '0/1' in text and '2 generated' in text
    assert 'not a hash check' in text and 'count (x)' in text


def test_system_plot_uses_actual_recorded_history_and_labels_its_span():
    now=10*86400
    info={'now':now,'backups':[{'created':now-86400,'stored_bytes':2000000},
                             {'created':now-8*86400,'stored_bytes':999999999},
                             {'created':now-200,'stored_bytes':None}]}
    ctx=views.Ctx(width=90,filt={'system_charts':True})
    text=render.plain(views.system_screen({'info':info},ctx).lines)
    assert '2 backups plotted' in text and '(MB, decimal)' in text and 'days before sample' in text
    assert 'integrity is checked separately' in text and 'last 7 days' not in text


def test_health_chart_no_outcomes_is_not_perfect_success():
    ctx=views.Ctx(width=90,filt={'health_charts':True})
    text=render.plain(views.health({'rows':[], 'extra':{}},ctx).lines)
    assert 'No observations' in text and 'n=0' in text and 'missing rates are not zero' in text


def test_health_trends_scope_epoch_arm_and_keep_missing_bins(tmp_path,make_contract,monkeypatch):
    from antslab.ledger import Ledger
    from antslab.tui.model import Source
    make_contract();wd=tmp_path/'run';l=Ledger(wd/'ledger.sqlite')
    # Finish one current trial plus a failed trial in another arm, one in another epoch.
    for arm,epoch,state in [('random','current','done'),('other','current','failed'),('random','old','failed')]:
        tid=l.enqueue('toy',arm,1,{'a':.5,'b':2,'c':'u'},'random',stage='s2',epoch=epoch)
        t=l.claim(60,arm=arm,epoch=epoch)
        l.finish(tid,t['attempt'],state,metrics={'val_mse':.8,'n_params':10})
    l.close()
    s=Source(wd,str(tmp_path/'task.toml'),'random')
    # Scope the view explicitly to the current epoch even though the last row is older.
    s.hist.epoch='current'
    out=s.health_extra(trends=True)
    assert sum(n for _,_,n in out['failure_trend'])==1
    assert all(rate==0 for _,rate,_ in out['failure_trend'])
    assert len(out['failure_trend'])==1 and len(out['finished_trend'])==12
    assert sum(n for _,n in out['finished_trend'])==1
    s.close()


def test_data_chart_uses_catalog_metadata_without_loading_any_dataset(monkeypatch):
    from antslab.tui import model
    from antslab.data import Catalog
    root=__import__('pathlib').Path(__file__).resolve().parents[1]
    monkeypatch.setattr(Catalog,'load',lambda *a,**k: (_ for _ in ()).throw(AssertionError('loaded series')))
    o=model.data_overview(str(root/'data/catalog.toml'))
    assert o['families'] and sum(sum(c.values()) for c in o['families'].values())==sum(len(v) for v in o['roles'].values())
    for width in (72,120):
        ctx=views.Ctx(width=width,height=30,filt={'data_charts':True})
        v=views.data_screen({'data':o},ctx)
        text=render.plain(views.fit(v.lines,width))
        assert 'Family / catalog group' in text and 'not a hash check' in text
        assert len(v.lines)<=ctx.height-3


def test_default_dashboards_include_data_and_plot_without_paging():
    now=100000
    data={'roles':{'dev':[('etth1',True)],'sealed':[('hidden',False)]},'raw_bytes':1e6,'processed':2,
          'families':{'electricity':{'dev':1,'sealed':1}}}
    health={'rows':[('FAIL','A failed check'),('OK','Another check')],
            'extra':{'failure_trend':[(-.5,20,5)],'finished_trend':[(-.5,5)]}}
    info={'version':'0.6.4','now':now,'workdir':'e44a/random_s101','backup_dir':'backups/series/e44a/random_s101',
          'backup_state':'available','backups':[{'created':now-600,'stored_bytes':2e6,'present':True}]}
    for width,height in [(80,24),(100,30),(150,40)]:
        ctx=views.Ctx(width=width,height=height)
        for v,words in [(views.health(health,ctx),['A failed check','Failed / finished','Finished trials']),
                        (views.data_screen({'data':data},ctx),['etth1','count (x)']),
                        (views.system_screen({'info':info},ctx),['e44a','1 backups plotted','minutes before sample'])]:
            text=render.plain(v.lines)
            assert all(word in text for word in words),text
            assert len(v.lines)<=height-3


def test_empty_backup_states_are_complete_and_do_not_claim_a_time_window():
    for state,phrase in [('missing','not created'),('empty','readable'),('unreadable','Cannot read'),('invalid_metadata','sidecars')]:
        text=render.plain(views.system_screen({'info':{'backup_state':state,'backups':[]}},views.Ctx(width=80,height=24)).lines)
        assert phrase in text and 'No time range' in text
        assert '7 days' not in text and 'No observations' not in text


def test_activity_query_scopes_arm_epoch_and_deduplicates_sources(tmp_path,make_contract):
    import time
    from antslab.ledger import Ledger
    from antslab.tui.model import Source,research_activity
    make_contract();wd=tmp_path/'activity';l=Ledger(wd/'ledger.sqlite')
    for arm,epoch,cpu in [('random','current',60),('other','current',9000),('random','old',9000)]:
        tid=l.enqueue('toy',arm,1,{'a':.5,'b':2,'c':'u'},'random',stage='s2',epoch=epoch)
        t=l.claim(60,arm=arm,epoch=epoch)
        l.finish(tid,t['attempt'],'done',metrics={'val_mse':.8,'n_params':10},cpu_s=cpu)
    src=Source(wd,str(tmp_path/'task.toml'),'random');src.hist.epoch='current'
    before=l.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]
    try:
        result=research_activity([src,src],time.time()+1)
        assert result['sources']==1 and result['unreadable']==0
        assert sum(c['cpu_s'] for c in result['cells'])==60
        assert sum(c['done'] for c in result['cells'])==1
        assert l.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]==before
    finally:src.close();l.close()


def test_activity_grid_distinguishes_coverage_and_uses_visible_dates_only():
    import datetime
    now=datetime.datetime.now().replace(hour=12,minute=0,second=0,microsecond=0)
    data={'now':now.timestamp(),'started_at':now.timestamp()-7200,'timezone':'local',
          'sources':1,'unreadable':0,'cells':[{'day':now.strftime('%Y-%m-%d'),'hour':11,
                     'done':2,'cpu_s':90,'cpu_known':2,'terminal':2},
                     {'day':(now-datetime.timedelta(days=5)).strftime('%Y-%m-%d'),'hour':11,
                     'done':999,'cpu_s':99999,'cpu_known':1,'terminal':1}]}
    grid=charts.activity_grid(data,80,2);text=render.plain(grid)
    assert 'CPU minutes' in text and 'peak 1.5' in text and 'total 1.5' in text
    assert '- outside coverage' in text and '█' in text and '999' not in text
    assert grid[3][10][0]=='- ' and grid[3][11][0]=='· ' and grid[3][12][0]=='█ '
    trials=render.plain(charts.activity_grid(data,80,2,'trials'))
    assert 'completed trials' in trials and 'total 2' in trials
    info={'version':'0.6.4','now':now.timestamp(),'activity':data,'backup_state':'empty','backups':[]}
    for width,height in [(80,24),(100,30)]:
        v=views.system_screen({'info':info},views.Ctx(width=width,height=height))
        assert len(v.lines)<=height-3 and 'Backup receipts' in render.plain(v.lines)


def test_registered_budget_completion_is_not_a_stuck_heartbeat(tmp_path,make_contract,monkeypatch):
    from types import SimpleNamespace
    from antslab import series
    from antslab.ledger import Ledger
    from antslab.tui.model import registered_budget
    c=make_contract();epoch='012345abcdef';wd=tmp_path/'series/e44a/random_s1'
    path=tmp_path/'e44a.toml'
    path.write_text(f'name="e44a"\nquestion="fixture"\narms=["random"]\nseeds="1"\nmax_cpu_s=60\nworkers=1\nepoch="{epoch}"\nbase="{wd.parent}"\n')
    monkeypatch.setattr(series,'find_series_file',lambda *a:path)
    l=Ledger(wd/'ledger.sqlite');src=SimpleNamespace(workdir=wd,c=c,hist=SimpleNamespace(epoch=epoch),arm='random',led=l)
    for cpu in (59,1):
        tid=l.enqueue('toy','random',1,{'a':.5,'b':2,'c':'u'},'random',stage='s2',epoch=epoch)
        t=l.claim(60,arm='random',epoch=epoch);l.finish(tid,t['attempt'],'done',cpu_s=cpu)
        result=registered_budget(src)
        assert (result is None) if cpu==59 else result=={'used':60,'limit':60}
    src.hist.epoch='different';assert registered_budget(src) is None
    l.close()

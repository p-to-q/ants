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


def test_system_plot_uses_only_recent_recorded_sizes():
    now=10*86400
    info={'now':now,'backups':[{'created':now-86400,'stored_bytes':2000000},
                             {'created':now-8*86400,'stored_bytes':999999999},
                             {'created':now-200,'stored_bytes':None}]}
    ctx=views.Ctx(width=90,filt={'system_charts':True})
    text=render.plain(views.system_screen({'info':info},ctx).lines)
    assert '1 recorded backups' in text and '(MB, decimal)' in text and 'days before sample' in text
    assert 'integrity is checked separately' in text and '999' not in text


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

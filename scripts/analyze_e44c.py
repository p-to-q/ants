#!/usr/bin/env python3
"""Registered E44c final analysis. Refuses incomplete budgets or identities."""
import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import statistics
from antslab.ledger import Ledger
from antslab.series import load_series


def signed_rank_p(differences):
    values=[x for x in differences if x!=0]
    if not values:return 1.
    if len(values)>16:raise ValueError('exact enumeration is bounded')
    order=sorted(range(len(values)),key=lambda i:abs(values[i]));ranks=[0.]*len(values);i=0
    while i<len(order):
        j=i+1
        while j<len(order) and abs(values[order[j]])==abs(values[order[i]]):j+=1
        for index in order[i:j]:ranks[index]=(i+1+j)/2
        i=j
    observed=abs(sum(r if v>0 else -r for r,v in zip(ranks,values)))
    return sum(abs(sum(s*r for s,r in zip(signs,ranks)))>=observed-1e-12
               for signs in itertools.product((-1,1),repeat=len(values)))/(2**len(values))


def read_run(path,arm,epoch,budget):
    ledger=Ledger.open_readonly(path);ledger.conn.execute('BEGIN')
    try:
        if not ledger.verify_chain()[0]:raise ValueError('broken chain')
        if ledger.conn.execute('select count(*) from unseals').fetchone()[0] or ledger.conn.execute('select count(*) from llm_calls').fetchone()[0]:raise ValueError('unexpected data/model access')
        epochs={r[0] for r in ledger.conn.execute('select distinct epoch from trials')}
        if epochs!={epoch}:raise ValueError('cross-epoch evidence')
        starts=[e['payload'] for e in ledger.events('daemon_started')]
        if not starts or any(e.get('arm')!=arm for e in starts) or len({e.get('arm_def_hash') for e in starts})!=1:raise ValueError('changed run definition')
        cpu=0.;points=[];counts={}
        for row in ledger.conn.execute("select state,cpu_s,stage,metrics from trials where state in ('done','failed','timeout','crashed') order by finished_at,id"):
            counts[row[0]]=counts.get(row[0],0)+1;cpu+=row[1] or 0.;m=json.loads(row[3] or '{}')
            if row[0]=='done' and row[2]=='s2' and 0<=m.get('n_params',math.inf)<=300 and math.isfinite(m.get('rel_mse',math.inf)) and m['rel_mse']>0:
                points.append((cpu,m['rel_mse']))
        if cpu<budget:raise ValueError('budget not complete')
        endpoint=lambda b:min((v for c,v in points if c<=b),default=None)
        usage=[e['payload'] for e in ledger.events('controller_usage') if e['payload'].get('epoch')==epoch]
        return {'cpu_s':cpu,'at_B':endpoint(7200),'at_2B':endpoint(14400),'outcomes':counts,
                'controller_usage':usage,'usage_complete':len(usage)==len(starts),'arm_hash':starts[0]['arm_def_hash']}
    finally:ledger.close()


def analyze(base,series,registration=None):
    if series.get('arms')!=['portfolio','tpe_reference_v2','random'] or series['seeds_parsed']!=list(range(301,309)) or series['max_cpu_s']!=7200 or series.get('arm_cpu_s',{})!={'random':14400}:raise ValueError('not the registered E44c design')
    registration=json.loads(Path(registration or Path(base)/'registration.json').read_text())
    if registration['epoch']!=series['epoch'] or registration['seeds']!=series['seeds_parsed'] or set(registration['arm_definitions'])!={'portfolio','tpe_reference_v2','random'}:
        raise ValueError('registration identity mismatch')
    for field, path in [('protocol_sha256',series['protocol']),('contract_sha256',series['contract']),('analysis_sha256',__file__)]:
        if registration.get(field)!=hashlib.sha256(Path(path).read_bytes()).hexdigest():
            raise ValueError(f'frozen file identity mismatch: {field}')
    runs={};definitions={}
    for seed in series['seeds_parsed']:
        for arm in ('portfolio','tpe_reference_v2','random'):
            row=read_run(Path(base)/f'{arm}_s{seed}'/'ledger.sqlite',arm,series['epoch'],series['arm_cpu_s'].get(arm,7200));runs[f'{arm}_s{seed}']=row
            if arm in definitions and definitions[arm]!=row['arm_hash']:raise ValueError('definition differs across seeds')
            if registration['arm_definitions'][arm]!=row['arm_hash']:raise ValueError('arm differs from registration')
            definitions[arm]=row['arm_hash']
    if any(r['at_B'] is None for r in runs.values()) or any(runs[f'random_s{s}']['at_2B'] is None for s in series['seeds_parsed']):
        return {'decision':'incomplete_endpoint_evidence','adopt_portfolio':False,'runs':runs}
    tests=[]
    for name,arm,key in [('optuna_B','tpe_reference_v2','at_B'),('random_2B','random','at_2B')]:
        ds=[math.log(runs[f'portfolio_s{s}']['at_B']/runs[f'{arm}_s{s}'][key]) for s in series['seeds_parsed']]
        tests.append({'reference':name,'log_ratios':ds,'median_ratio':math.exp(statistics.median(ds)),'p':signed_rank_p(ds)})
    adjusted=0
    for i,index in enumerate(sorted(range(2),key=lambda i:tests[i]['p'])):
        adjusted=max(adjusted,min(1,tests[index]['p']*(2-i)));tests[index]['holm_p']=adjusted
    passed=all(t['median_ratio']<=.99 and t['holm_p']<=.05 for t in tests)
    return {'decision':'search_gate_passed' if passed else 'no_portfolio_adoption','adopt_portfolio':passed,
            'quality_status':'development_search_comparison_only','tests':tests,'runs':runs,
            'limits':'No model deployment, confirmation or generalization claim. Physical checkpoint acceptance remains separate.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',required=True);p.add_argument('--series',required=True);p.add_argument('--out',required=True);p.add_argument('--registration');a=p.parse_args()
    out=Path(a.out)
    if out.exists():raise FileExistsError('use a new immutable analysis receipt')
    result=analyze(a.base,load_series(a.series),a.registration);out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x') as f:json.dump(result,f,indent=2,allow_nan=False);f.write('\n')

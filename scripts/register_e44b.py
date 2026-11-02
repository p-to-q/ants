#!/usr/bin/env python3
"""Write immutable E44b registration before any scientific worker starts."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import platform
import re
from antslab.series import load_series,compute_epoch
from antslab.contract import load_contract
from antslab.proposers import make_proposer
from antslab.campaign import Campaign
from antslab import __version__


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def register(series_path,source_archive,commit,out):
    s=load_series(series_path)
    if s['arms']!=['portfolio','tpe_reference','random'] or s['workers']!=2 or s['name']!='e44b' or s['seeds_parsed']!=list(range(201,209)) or s['max_cpu_s']!=7200 or s['arm_cpu_s']!={'random':14400}:raise ValueError('unregistered design')
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('expected a full source commit')
    c=load_contract(s['contract']);epoch=compute_epoch(s['contract'])
    if epoch!=s['epoch'] or c.caps!=[300]:raise ValueError('evaluator identity or common cap mismatch')
    definitions={}
    for arm in s['arms']:
        definition=Campaign.definition_of(arm,make_proposer(arm,c,201),c)
        definitions[arm]=hashlib.sha256(json.dumps(definition,sort_keys=True).encode()).hexdigest()[:12]
    document={'schema':1,'study':'E44b','epoch':epoch,'source_commit':commit,
        'source_archive_sha256':sha(source_archive),'series_sha256':sha(series_path),
        'protocol_sha256':sha(s['protocol']),'contract_sha256':sha(s['contract']),
        'seeds':s['seeds_parsed'],'arm_definitions':definitions,'B_cpu_s':7200,
        'arm_cpu_s':{a:s['arm_cpu_s'].get(a,s['max_cpu_s']) for a in s['arms']},
        'version':__version__,'python':platform.python_version(),'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
    if any(out.parent.glob('*/ledger.sqlite')):raise ValueError('scientific workers already initialized; cannot preregister retrospectively')
    if out.exists():
        old=json.loads(out.read_text())
        if {k:v for k,v in old.items() if k!='created_utc'}!={k:v for k,v in document.items() if k!='created_utc'}:raise ValueError('registration conflict')
        return old
    with out.open('x') as f:json.dump(document,f,indent=2,allow_nan=False);f.write('\n')
    return document

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--series',required=True);p.add_argument('--source-archive',required=True)
    p.add_argument('--commit',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    print(json.dumps(register(a.series,a.source_archive,a.commit,a.out),indent=2))

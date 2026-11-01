#!/usr/bin/env python3
"""Stable deployment status; deliberately independent of the replaced Python environment."""
import argparse
import datetime
import json
import os
from pathlib import Path
import tempfile
import uuid

DEFAULT = '/var/lib/ants-deploy/state.json'
PHASES = ('preparing', 'installing', 'verifying', 'ready', 'failed')


def read(path):
    try:
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as stream:
            raw=stream.read(65537)
        if len(raw)>65536:raise ValueError('oversized record')
        data=json.loads(raw)
        if not isinstance(data,dict) or data.get('phase') not in PHASES:raise ValueError('invalid phase')
        return data
    except FileNotFoundError:
        return {'phase':'unrecorded','reason':'No deployment record; legacy installation.'}
    except (OSError,ValueError):
        return {'phase':'failed','reason':'Deployment record is unreadable or invalid; inspect with the administrator.'}


def write(path,phase,version=None,reason=None):
    path=Path(path);data=read(path)
    if phase=='preparing':data={'attempt':uuid.uuid4().hex[:12]}
    data['phase']=phase
    if version is not None:data['target_version']=str(version)[:64]
    if reason is not None:data['reason']=str(reason)[:300]
    elif phase=='ready':data.pop('reason',None)
    data['updated_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.deploy-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(data,stream,sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
            os.fchmod(stream.fileno(),0o644)
        os.replace(name,path)
        with (path.parent/'history.jsonl').open('a') as history:
            history.write(json.dumps(data,sort_keys=True)+'\n');history.flush();os.fsync(history.fileno())
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)
    return data


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=('check','status','set'))
    p.add_argument('phase',nargs='?',choices=PHASES)
    p.add_argument('--state',default=DEFAULT);p.add_argument('--version');p.add_argument('--reason');p.add_argument('--json',action='store_true')
    a=p.parse_args(argv)
    if a.action=='set':
        if not a.phase:p.error('set requires a phase')
        write(a.state,a.phase,a.version,a.reason);return 0
    data=read(a.state)
    ready=data['phase'] in ('ready','unrecorded')
    if a.action=='check' and ready:return 0
    if a.json:print(json.dumps(data,sort_keys=True))
    else:
        print(f"ants deployment: {data['phase']} · target {data.get('target_version','unknown')}")
        if data.get('updated_utc'):print('Updated '+data['updated_utc'])
        if data.get('reason'):print(data['reason'])
        if not ready:
            print('The installation is not ready. It may still be updating or may have been interrupted.')
            print('No onboarding or experiment command was started. Run `ants deploy-status` for status.')
    return 0 if ready or a.action=='status' else 75


if __name__=='__main__':raise SystemExit(main())

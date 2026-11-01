"""Explicit read-only ledger projection and bounded terminal map cache."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from pathlib import Path
import tempfile
import time

from .archive_index import _read
from .textsafe import loads_strict, clean_text

MAX_POINTS = 5000


def map_path():
    from .home import home
    explicit = os.environ.get('ANTS_SEARCH_MAP')
    personal = home()/'maps/current.json'
    return Path(explicit).expanduser() if explicit else personal if personal.exists() else Path('/var/lib/ants/maps/current.json')


def _finite(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)


def read_map(path=None):
    path=Path(path) if path is not None else map_path()
    try:
        obj=loads_strict(_read(path).decode(),max_chars=2<<20)
        if not isinstance(obj,dict) or obj.get('schema')!=1 or not isinstance(obj.get('points'),list) or len(obj['points'])>MAX_POINTS:
            raise ValueError('unsupported or oversized map cache')
        scope=obj.get('scope',{})
        if not isinstance(scope,dict) or not all(isinstance(scope.get(k),str) and scope[k] for k in ('epoch','contract_sha256','metric','stage')):
            raise ValueError('missing map evaluation scope')
        if not isinstance(obj.get('projection'),dict) or not all(_finite(scope.get(k)) and scope[k]>0 for k in ('cap','budget')):
            raise ValueError('invalid map projection or budget')
        ids=set()
        for p in obj['points']:
            if not isinstance(p,dict) or not all(_finite(p.get(k)) for k in ('u','v','value','cpu_s','n_params')) or p['cpu_s']<0 or p['n_params']<0:
                raise ValueError('invalid map observation')
            if not isinstance(p.get('source'),str) or not isinstance(p.get('trial'),int) or isinstance(p.get('trial'),bool):
                raise ValueError('missing map source/trial identity')
            if not all(isinstance(p.get(k),str) and p[k] for k in ('id','run','arm','spec_key')) or p['id'] in ids:
                raise ValueError('invalid or duplicate map identity')
            if p['cpu_s']>scope['budget'] or p['n_params']>scope['cap']:
                raise ValueError('map point exceeds declared budget/cap')
            if p.get('epoch')!=scope['epoch'] or not isinstance(p.get('spec'),dict):
                raise ValueError('map point crosses evaluation scope')
            ids.add(p['id'])
        return {**obj,'path':str(path)}
    except FileNotFoundError:
        return {'schema':1,'points':[],'missing':True,'path':str(path)}
    except (OSError,ValueError,TypeError,KeyError,UnicodeError) as e:
        return {'schema':1,'points':[],'error':clean_text(str(e),200),'path':str(path)}


def build_map(contract_path,workdirs,out,budget=50400,cap=3000):
    """An explicit retrospective snapshot, never a silent scan of live experiments."""
    import numpy as np
    from .contract import load_contract
    from .ledger import Ledger
    from .series import compute_epoch
    if not _finite(budget) or budget<=0 or not _finite(cap) or cap<=0:
        raise ValueError('map budget and cap must be positive finite numbers')
    c=load_contract(contract_path)
    if c.direction!='min' or not c.cap_metric:
        raise ValueError('map requires a minimised metric and declared parameter-count constraint')
    expected=compute_epoch(contract_path)
    points=[];sources=[];seen=set();run_identities=set()
    contract_hash=hashlib.sha256(Path(contract_path).read_bytes()).hexdigest()
    for workdir in workdirs:
        wd=Path(workdir).resolve();db=wd/'ledger.sqlite'
        if str(db) in seen:raise ValueError('duplicate map source')
        seen.add(str(db));led=Ledger.open_readonly(db)
        try:
            led.conn.execute('BEGIN')
            ok,bad=led.verify_chain()
            if not ok:raise ValueError(f'map source chain broken at {bad}')
            identities=led.conn.execute('SELECT DISTINCT gym,arm,epoch FROM trials').fetchall()
            if len(identities)!=1:raise ValueError('each map input must have one gym/arm/epoch')
            gym,arm,epoch=identities[0]
            seed=None
            if gym!=c.gym or epoch!=expected:raise ValueError('map source evaluation identity differs')
            # Bind snapshot to a chain head, rather than hashing a changing WAL database.
            head=led.head()
            first_hash=led.conn.execute("SELECT hash FROM events ORDER BY seq LIMIT 1").fetchone()[0]
            identity=(gym,arm,epoch,first_hash)
            if identity in run_identities:raise ValueError('duplicate ledger generation map input')
            run_identities.add(identity)
            sid=hashlib.sha256(json.dumps([*identity,contract_hash]).encode()).hexdigest()[:16]
            parents={r['spec_key']:r['origin_key'] for r in led.conn.execute("SELECT spec_key,origin_key FROM trials WHERE source!='promote' AND origin_key IS NOT NULL ORDER BY id")}
            cpu=0.
            for row in led.conn.execute("SELECT id,stage,metrics,cpu_s,spec_key,params FROM trials WHERE state='done' ORDER BY finished_at,id"):
                cpu+=row['cpu_s'] or 0
                if cpu>budget:break
                if row['stage']!=c.stages[-1]:continue
                m=loads_strict(row['metrics'] or '{}');value,size=m.get(c.primary),m.get(c.cap_metric)
                if not _finite(value) or not _finite(size) or not 0<size<=cap:continue
                spec=c.space.validate(loads_strict(row['params']))
                if c.space.key(spec)!=row['spec_key']:raise ValueError('map spec identity mismatch')
                points.append({'id':f'{sid}-{row["id"]}','run':wd.name,'source':sid,'arm':arm,'seed':seed,'epoch':epoch,
                               'trial':row['id'],'spec_key':row['spec_key'],'parent_key':parents.get(row['spec_key']),
                               'spec':spec,'value':value,'n_params':size,'cpu_s':cpu})
                if len(points)>MAX_POINTS:raise ValueError('map exceeds 5000 observations; choose fewer source runs')
            sources.append({'id':sid,'run':wd.name,'arm':arm,'seed':seed,'chain_head':head,'epoch':epoch})
        finally:led.close()
    if not points:raise ValueError('no eligible completed observations')
    fields=[]
    for name,p in c.space.params.items():
        fields.append((name,'active',None))
        if p.kind=='cat':fields.extend((name,'choice',v) for v in p.choices)
        else:fields.append((name,'number',None))
    vectors=[]
    for point in points:
        s=point['spec'];vector=[]
        for name,kind,value in fields:
            p=c.space.params[name];active=c.space.is_active(name,s)
            if kind=='active':vector.append(float(active))
            elif not active:vector.append(0.)
            elif kind=='choice':vector.append(float(type(s[name]) is type(value) and s[name]==value)/math.sqrt(len(p.choices)))
            elif p.kind=='bool':vector.append(float(s[name]))
            else:vector.append((s[name]-p.low)/max(1e-12,p.high-p.low))
        vectors.append(vector)
    matrix=np.asarray(vectors);mean=matrix.mean(0);z=matrix-mean
    _,singular,components=np.linalg.svd(z,full_matrices=False)
    axes=np.zeros((2,len(fields)))
    for i in range(min(2,len(components))):
        axes[i]=components[i]
        if axes[i,np.argmax(abs(axes[i]))]<0:axes[i]*=-1
    xy=z@axes.T
    for point,(u,v) in zip(points,xy):point.update(u=float(u),v=float(v))
    denom=float(sum(singular**2));variance=float(sum(singular[:2]**2)/denom) if denom else 0.
    obj={'schema':1,'label':'Retrospective search map','created_at':time.time(),
         'scope':{'epoch':expected,'contract_sha256':contract_hash,
                  'metric':c.primary,'stage':c.stages[-1],'direction':'min','cap':cap,'budget':budget,
                  'clock':'cumulative CPU of done trials, matching E43 registered analysis'},
         'projection':{'kind':'fixed_retrospective_pca','fields':fields,'mean':mean.tolist(),'components':axes.tolist(),
                       'encoded_variance':variance,'note':'Retrospective feature projection, not online evidence or a semantic distance.'},
         'sources':sources,'points':points}
    raw=(json.dumps(obj,allow_nan=False,separators=(',',':'))+'\n').encode()
    if len(raw)>2<<20:raise ValueError('map cache exceeds 2 MiB; choose fewer source runs')
    out=Path(out)
    if out.exists() or out.is_symlink():raise FileExistsError('use a new map generation; destination exists')
    out.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.map-',dir=out.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.link(temp,out)
        directory=os.open(out.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:Path(temp).unlink(missing_ok=True)
    return {'path':str(out),'points':len(points),'epoch':expected,'sha256':hashlib.sha256(raw).hexdigest()}


def cmd_map(a):
    try:
        if a.action=='build':result=build_map(a.contract,a.workdir,a.out,a.budget,a.cap)
        else:
            result=read_map(a.cache)
            if result.get('error') or result.get('missing'):
                print(json.dumps(result));return 1
        if a.action=='show' and not a.json:
            from .tui import views,render
            ctx=views.Ctx(width=max(40,min(a.width,240)),height=max(20,min(a.height,80)),ascii_only=a.ascii,filt={'map_height':a.height_mode})
            print(render.plain(views.fit(views.search_map_screen({'map':result},ctx).lines,ctx.width)))
        elif a.json:
            print(json.dumps(result,ensure_ascii=True,indent=2))
        else:
            print(f"map: {result['points']} observations, epoch {result['epoch']}, saved {clean_text(result['path'],200)}")
        return 0
    except (OSError,ValueError,TypeError,ImportError,sqlite3.Error) as e:
        print(json.dumps({'error':clean_text(str(e),200)}));return 1

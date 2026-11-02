"""Bounded checkpoint inventories and protected ledger registration, opt-in only."""
import hashlib
import json
from pathlib import Path


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write_inventory(out):
    out=Path(out);files=[]
    for p in sorted(out.rglob('*')):
        rel=p.relative_to(out)
        if p.is_file() and (rel.parts[0] in ('source','checkpoints','coverage_windows') or rel.name in ('coverage.json','checkpoint.json','checkpoint.npz','source.py','context.json')):
            files.append({'path':str(rel),'sha256':digest(p),'bytes':p.stat().st_size})
    if not files:raise ValueError('no checkpoint evidence')
    (out/'checkpoint_inventory.json').write_text(json.dumps({'schema':1,'files':files},sort_keys=True)+'\n')
    return verify_inventory(out)


def verify_inventory(out, expected=None):
    out=Path(out);index=out/'checkpoint_inventory.json'
    if index.is_symlink() or index.stat().st_size>1<<20:raise ValueError('invalid inventory')
    if expected is not None and digest(index)!=expected:raise ValueError('inventory identity changed')
    obj=json.loads(index.read_text());items=obj.get('files',[])
    if obj.get('schema')!=1 or not isinstance(items,list) or not 1<=len(items)<=500:raise ValueError('invalid inventory schema')
    total=0;seen=set();paths=[]
    for item in items:
        rel=Path(item['path'])
        if rel.is_absolute() or '..' in rel.parts or str(rel) in seen:raise ValueError('unsafe or duplicate checkpoint path')
        seen.add(str(rel));p=out/rel
        if p.is_symlink() or any(a.is_symlink() for a in p.parents if a!=out and a.is_relative_to(out)):raise ValueError('checkpoint symlink')
        size=p.stat().st_size;total+=size
        if not p.is_file() or size>16<<20 or total>64<<20 or size!=item['bytes'] or digest(p)!=item['sha256']:raise ValueError('checkpoint file mismatch or limit')
        paths.append(p)
    return digest(index),paths


def register(ledger,trial_id,out,expected):
    out=Path(out);sha,paths=verify_inventory(out,expected)
    # Inventory is registered last: its presence proves the full set was registered.
    for p in paths+[out/'checkpoint_inventory.json']:
        old=ledger.artifact(str(p))
        if old:
            if old['sha256']!=digest(p):raise ValueError('registered checkpoint changed')
            continue
        ledger.add_artifact(trial_id,p,'checkpoint',protected=True)
    return len(paths)

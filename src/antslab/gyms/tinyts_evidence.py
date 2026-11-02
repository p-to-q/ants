"""TinyTS scoring unchanged; add per-task receipts and original S2 fitted states."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
import numpy as np
from . import tinyts as T
from ..model_artifact import save_refit, load
from ..checkpoint_store import write_inventory


def evaluate(spec,stage,out,strict_folds=False):
    out=Path(out);out.mkdir(parents=True,exist_ok=True);cfg=T.CAT_STAGES[stage];cat=T.catalog()
    context=json.loads((out/'context.json').read_text()) if (out/'context.json').is_file() else {}
    declared_caps=context.get('contract',{}).get('objective',{}).get('caps',[100,300,1000,3000])
    if not declared_caps or any(isinstance(c,bool) or not isinstance(c,(int,float)) or not np.isfinite(c) or c<=0 for c in declared_caps):raise ValueError('invalid capture caps')
    cat.require_roles(cfg['names'],{'dev'})
    if stage!='s2' and not strict_folds:
        metrics=T.evaluate_catalog(spec,cfg['names'],'val',cfg['H'],cfg['cap_tr'],cfg['cap_va'],deep=False,folds=cfg.get('folds',1),per_dataset=True)
        (out/'metrics.json').write_text(json.dumps(metrics,allow_nan=False)+'\n');return metrics
    tasks=[];training_hashes={};skipped=0
    for name in cfg['names']:
        training_hashes={};ser=cat.load(name);mu=ser.values[:ser.n_train].mean(0);sd=ser.values[:ser.n_train].std(0)+1e-8
        blocks=T.rolling_blocks(ser,cfg.get('folds',1))
        for horizon in (cfg['H'] or ser.horizons):
            for fold,(lo,hi) in enumerate(blocks):
                end=lo if cfg.get('folds',1)>1 else ser.n_train
                if hi-lo<horizon+8 or end<2*horizon+16:
                    skipped+=1;continue
                if end not in training_hashes:training_hashes[end]=hashlib.sha256(ser.values[:end].tobytes()).hexdigest()
                norm_end=end if strict_folds else ser.n_train
                fold_mean=ser.values[:norm_end].mean(0);fold_scale=ser.values[:norm_end].std(0)+1e-8
                tasks.append({'family':ser.family,'normalization_profile':'fold_train_prefix' if strict_folds else 'legacy_dataset_train','training_data_sha256':training_hashes[end],'dataset':name,'horizon':int(horizon),'fold':fold,'train_end':int(end),
                    'validation_bounds':[int(lo),int(hi)],'channels':ser.values.shape[1],
                    'normalisation_train_end':norm_end,'mean':fold_mean,'scale':fold_scale})
    records=[];original=T._score
    def score(fitted,x,y,H,rp,keep=False,Xref=None):
        result=original(fitted,x,y,H,rp,keep=stage=='s2' or keep,Xref=Xref)
        if len(records)>=len(tasks):raise ValueError('unexpected scoring task')
        task=tasks[len(records)];mu=task['mean'];sd=task['scale']
        row={k:v for k,v in task.items() if k not in ('mean','scale')}
        row.update(mse=float(result[0]),mae=float(result[1]),reference_mse=float(result[2]),windows=len(x),validation_lookback=int(x.shape[1]),reference_period=int(rp),training_lookbacks=[int(m.L) for m in fitted])
        row.update(training_sample_seed=11+task['fold'],validation_sample_seed=21+task['fold'],training_window_cap=cfg['cap_tr'],validation_window_cap=cfg['cap_va'])
        if stage=='s2':
            losses=np.empty(len(y),dtype=np.float32)
            for start in range(0,len(y),128):
                sl=slice(start,start+128);losses[sl]=np.mean((result[4][sl]-y[sl])**2,axis=1)
            lossfile=out/'coverage_windows'/f'task_{len(records):03d}.npz';lossfile.parent.mkdir(parents=True,exist_ok=True)
            np.savez_compressed(lossfile,mse=losses)
            row['window_metrics']=str(lossfile.relative_to(out))
        if H!=task['horizon']:raise ValueError('task ordering differs from evaluator')
        if stage=='s2':
            nominal=T.count_params(spec);cap=next((c for c in sorted(declared_caps) if nominal<=c),None)
            dest=out/'checkpoints'/f'task_{len(records):03d}'
            if cap is None:row.update(checkpoint='rejected',reason='nominal cap exceeded')
            else:
                probe=np.repeat(mu[:,None],max(m.L for m in fitted),axis=1)
                try:
                    receipt=save_refit(fitted,mu,sd,probe,dest,cap,row,origin='evaluation_checkpoint',copy_source=False)
                    # Check actual scoring predictions on a small fixed validation probe.
                    lo,hi=task['validation_bounds'];starts=np.arange(max(lo,x.shape[1]),hi-H+1)
                    total=len(starts)*len(mu)
                    flat=np.sort(np.random.default_rng(21+task['fold']).choice(total,size=len(x),replace=False)) if len(x)<total else np.arange(total)
                    ids=(flat//len(starts))[:8];raw=(Xref if Xref is not None else x)[:8]*sd[ids,None]+mu[ids,None]
                    actual=load(dest).predict(raw,ids);expected=result[4][:8]*sd[ids,None]+mu[ids,None]
                    error=float(np.max(np.abs(actual-expected)))
                    if not np.allclose(actual,expected,rtol=1e-6,atol=1e-5):raise ValueError('scoring prediction parity failed')
                    row.update(checkpoint='saved',path=str(dest.relative_to(out)),stored_numeric_values=receipt['stored_numeric_values'],scoring_max_abs_error=error)
                except ValueError as e:row.update(checkpoint='rejected',reason=str(e))
        records.append(row)
        return result
    T._score=score
    try:
        if not strict_folds:
            metrics=T.evaluate_catalog(spec,cfg['names'],'val',cfg['H'],cfg['cap_tr'],cfg['cap_va'],deep=stage=='s2',folds=cfg.get('folds',1),per_dataset=True)
        else:
            from ..data import Series
            from .. import metrics as M
            results=[];started=time.perf_counter();catalog_fn=T.catalog
            class FoldCatalog:
                def __getattr__(self,name):return getattr(cat,name)
                def load(self,name):
                    ser=cat.load(name)
                    if name!=task['dataset']:return ser
                    lo,hi=task['validation_bounds']
                    return Series(ser.name,ser.family,ser.role,ser.values[:hi],lo,hi-lo,ser.ref_period,ser.horizons)
            try:
                for task in tasks:
                    T.catalog=lambda:FoldCatalog()
                    results.append(T.evaluate_catalog(spec,[task['dataset']],'val',[task['horizon']],cfg['cap_tr'],cfg['cap_va'],deep=False,folds=1,tag=task['fold']))
            finally:T.catalog=catalog_fn
            if not results:raise ValueError('no usable chronological tasks')
            weights=M.family_weights([t['family'] for t in tasks]);nominal=T.count_params(spec)
            metrics={'rel_mse':M.gmre([r['rel_mse'] for r in results],weights),
                     'rel_mae':M.gmre([r['rel_mae'] for r in results],weights),
                     'val_mse':float(np.mean([r['val_mse'] for r in results])),
                     'n_params':nominal,'bytes':int(nominal*T.bits(spec)/8),
                     'n_tasks':len(results),'n_skipped':skipped,'fit_s':time.perf_counter()-started}
            for task,r in zip(tasks,results):
                key=f"rel__{task['dataset']}@{task['horizon']}"
                metrics.setdefault(key,[]).append(r['rel_mse'])
            for key in list(metrics):
                if key.startswith('rel__'):metrics[key]=float(np.exp(np.mean(np.log(metrics[key]))))
    finally:T._score=original
    if len(records)!=len(tasks):raise ValueError('incomplete task coverage')
    if stage=='s2':
        for file in Path(__file__).parent.parent.rglob('*.py'):
            if file.is_symlink():raise ValueError('source symlink')
            target=out/'source/antslab'/file.relative_to(Path(__file__).parent.parent);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,target)
        if hasattr(cat,'path'):
            for name in ('catalog.toml','catalog_synth.toml','MANIFEST.sha256','sources.toml'):
                source=cat.path.parent/name
                if source.is_file():
                    dest=out/'source/data'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
        (out/'coverage.json').write_text(json.dumps({'schema':1,'stage':stage,'kind':'evaluation_task_coverage','tasks':records},allow_nan=False,indent=2)+'\n')
        write_inventory(out)
        metrics['checkpoint_saved']=sum(r.get('checkpoint')=='saved' for r in records)
        metrics['checkpoint_rejected']=sum(r.get('checkpoint')=='rejected' for r in records)
    (out/'metrics.json').write_text(json.dumps(metrics,allow_nan=False)+'\n')
    return metrics


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',required=True);p.add_argument('--spec',required=True);p.add_argument('--out',required=True)
    p.add_argument('--strict-folds',action='store_true');p.add_argument('--seed',type=int,default=0);p.add_argument('--attempt',type=int,default=1);a=p.parse_args()
    evaluate(json.loads(Path(a.spec).read_text()),a.stage,a.out,a.strict_folds)
if __name__=='__main__':main()

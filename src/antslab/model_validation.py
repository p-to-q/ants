"""Explicit quality check of saved refit weights on their own dev validation split."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

import numpy as np


def evaluate(bundle,out,max_windows=2048):
    from .model_artifact import load, _read, _hash
    from .gyms.tinyts import catalog
    from .data import role_policy
    if not isinstance(max_windows,int) or isinstance(max_windows,bool) or not 1<=max_windows<=4096:
        raise ValueError('max_windows must be 1..4096')
    dest=Path(out)
    if dest.exists() or dest.is_symlink():raise FileExistsError('use a new validation receipt path')
    bundle=Path(bundle)
    predictor=load(bundle)
    manifest=_read(bundle/'manifest.json');provenance=manifest.get('provenance',{})
    dataset=provenance.get('dataset');horizon=provenance.get('horizon')
    if not isinstance(dataset,str) or not isinstance(horizon,int) or isinstance(horizon,bool) or horizon not in (96,192,336,720):
        raise ValueError('bundle is missing dataset/horizon provenance')
    if any(m.H!=horizon for m in predictor.models):raise ValueError('bundle horizon provenance differs from fitted weights')
    cat=catalog();cat.require_roles([dataset],{'dev'})
    with role_policy({'dev'}):ser=cat.load(dataset)
    train=ser.values[:ser.n_train]
    if hashlib.sha256(np.ascontiguousarray(train).tobytes()).hexdigest()!=provenance.get('training_data_sha256'):
        raise ValueError('bundle training data differs from the catalog')
    width=max(m.L for m in predictor.models);period=min(ser.ref_period,width)
    end=ser.n_train+ser.n_val
    origins=list(range(ser.n_train,end-horizon+1,max(1,horizon)))
    if not origins or width>ser.n_train:raise ValueError('no eligible validation windows')
    channels=ser.values.shape[1];total=len(origins)*channels
    rng=np.random.default_rng(20261102)
    selected=np.sort(rng.choice(total,size=min(total,max_windows),replace=False))
    origins_array=np.array([origins[int(i)//channels] for i in selected]);ids=np.array([int(i)%channels for i in selected])
    x=np.array([ser.values[t-width:t,ch] for t,ch in zip(origins_array,ids)])
    y=np.array([ser.values[t:t+horizon,ch] for t,ch in zip(origins_array,ids)])
    pred=predictor.predict(x,ids)
    baseline=np.tile(x[:,-period:],(1,(horizon+period-1)//period))[:,:horizon]
    scale=predictor.scale[ids,None];res=(pred-y)/scale;naive=(baseline-y)/scale
    if not np.isfinite(res).all() or not np.isfinite(naive).all():raise ValueError('nonfinite validation prediction')
    mse=float(np.mean(res**2));mae=float(np.mean(abs(res)));ref=float(np.mean(naive**2))
    hist,edges=np.histogram(res,bins=20)
    report={'schema':1,'kind':'saved_refit_dev_validation','created_at':time.time(),
        'bundle_manifest_sha256':_hash(bundle/'manifest.json'),'weights_sha256':_hash(bundle/'weights.npz'),
        'candidate_id':provenance.get('candidate_id'),'dataset':dataset,'horizon':horizon,'role':'dev','split':'val',
        'protocol':{'sampling':'nonoverlapping H-step origins, deterministic uniform channel/window subsample','seed':20261102,
                    'available_windows':total,'evaluated_windows':len(selected),'max_windows':max_windows,
                    'target_bounds':[ser.n_train,end],'training_data_sha256':provenance['training_data_sha256'],
                    'note':'Descriptive saved-weight check; not the historical S2 score, confirm evaluation or a published benchmark.'},
        'metrics':{'mse_std':mse,'mae_std':mae,'seasonal_naive_mse_std':ref,'rel_mse':mse/max(ref,1e-12)},
        'per_channel':[{'channel':ch,'windows':int(sum(ids==ch)),'mse_std':float(np.mean(res[ids==ch]**2))} for ch in sorted(set(ids.tolist()))],
        'trace':{'channel':int(ids[0]),'target_start':int(origins_array[0]),'steps':list(range(1,horizon+1)),
                 'actual':y[0].tolist(),'forecast':pred[0].tolist(),'seasonal_naive':baseline[0].tolist(),'units':'raw dataset units'},
        'residual_histogram':{'counts':hist.tolist(),'edges':edges.tolist(),'units':'training-standardized residual'},
        'quality_status':'development_only','release_ready':False}
    raw=(json.dumps(report,allow_nan=False,separators=(',',':'))+'\n').encode()
    if len(raw)>2<<20:raise ValueError('validation receipt too large')
    dest.parent.mkdir(parents=True,exist_ok=True);fd,temp=tempfile.mkstemp(prefix='.validation-',dir=dest.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.link(temp,dest)
        directory=os.open(dest.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:Path(temp).unlink(missing_ok=True)
    return report

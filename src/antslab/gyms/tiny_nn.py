"""Independent synthetic chronological neural-training gym; never used by E44b."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import numpy as np

def init(seed=101, width=8):
    rng = np.random.default_rng(seed)
    return {'w1': rng.normal(0, .25, (16, width)), 'b1': np.zeros(width),
            'w2': rng.normal(0, .25, (width, 1)), 'b2': np.zeros(1)}


def predict(weights, x):
    return np.tanh(x @ weights['w1'] + weights['b1']) @ weights['w2'] + weights['b2']


def loss_grad(weights, x, y):
    h = np.tanh(x @ weights['w1'] + weights['b1'])
    err = h @ weights['w2'] + weights['b2'] - y
    dy = 2 * err / err.size
    dh = (dy @ weights['w2'].T) * (1 - h * h)
    return float(np.mean(err * err)), {'w1': x.T @ dh, 'b1': dh.sum(0),
                                     'w2': h.T @ dy, 'b2': dy.sum(0)}


def train(x, y, steps=600, seed=101, width=8):
    weights = init(seed, width); curve = []
    for step in range(steps):
        loss, grad = loss_grad(weights, x, y)
        if not np.isfinite(loss) or any(not np.isfinite(v).all() for v in grad.values()):
            raise ValueError('non-finite training state')
        if step % 20 == 0: curve.append({'step': step, 'train_mse': loss})
        for key in weights: weights[key] -= .02 * grad[key]
    curve.append({'step': steps, 'train_mse': loss_grad(weights, x, y)[0]})
    return weights, curve



def evaluate(spec, stage, out, seed=101):
    if spec.get('kind') not in ('mlp','ridge') or type(spec.get('width')) is not int or not 1<=spec['width']<=8:
        raise ValueError('invalid neural spec')
    if stage not in ('s0','s1','s2'):raise ValueError('invalid stage')
    out=Path(out)
    if any((out/n).exists() for n in ('metrics.json','checkpoint.npz','checkpoint.json')):raise FileExistsError('use a fresh evaluation attempt directory')
    out.mkdir(parents=True,exist_ok=True)
    rng=np.random.default_rng(20261103);series=np.zeros(1536)
    for t in range(2,len(series)):
        series[t]=.6*series[t-1]+.25*np.tanh(2*series[t-2])+rng.normal(0,.1)
    # Test segment [1280,1536) is generated but never fitted or scored.
    origins=np.arange(16,1024);x=np.array([series[t-16:t] for t in origins]);y=series[origins,None]
    mean=x.mean(0);scale=x.std(0)+1e-8;ym=float(y.mean());ys=float(y.std()+1e-8)
    xx=(x-mean)/scale;yy=(y-ym)/ys
    steps={'s0':100,'s1':300,'s2':600}[stage]
    if spec['kind']=='mlp':
        weights,curve=train(xx,yy,steps,seed,spec['width'])
        forward=lambda v:predict(weights,v)
    else:
        design=np.column_stack([xx,np.ones(len(xx))]);coef=np.linalg.solve(design.T@design+1e-3*np.eye(17),design.T@yy)
        weights={'coef':coef};curve=[]
        forward=lambda v:np.column_stack([v,np.ones(len(v))])@coef
    arrays={**weights,'mean':mean,'scale':scale,'target_mean':np.array(ym),'target_scale':np.array(ys)}
    count=sum(v.size for v in arrays.values())
    if count>300:raise ValueError('physical cap exceeded')
    losses=[];naive=[];starts=list(range(1024,1280-16+1,16))
    for t in starts:
        context=series[t-16:t].copy();pred=[]
        for _ in range(16):
            value=float(forward(((context-mean)/scale)[None])[0,0]*ys+ym)
            pred.append(value);context=np.r_[context[1:],value]
        target=series[t:t+16]
        losses.append(float(np.mean((np.array(pred)-target)**2)))
        naive.append(float(np.mean((series[t-1]-target)**2)))
    metric={'val_mse':float(np.mean(losses)),'rel_mse':float(np.mean(losses)/np.mean(naive)),
            'n_params':int(count),'n_tasks':1,'n_windows':len(starts)}
    if not all(np.isfinite(v) for v in metric.values()):raise ValueError('non-finite validation')
    if stage=='s2':
        np.savez(out/'checkpoint.npz',**arrays)
        with np.load(out/'checkpoint.npz',allow_pickle=False) as z:
            assert all(np.array_equal(z[k],v) for k,v in arrays.items())
        shutil.copy2(__file__,out/'source.py')
        for file in Path(__file__).parent.parent.rglob('*.py'):
            if file.is_symlink():raise ValueError('source symlink')
            target=out/'source/antslab'/file.relative_to(Path(__file__).parent.parent);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,target)
        receipt={'schema':1,'kind':'evaluation_checkpoint','spec':spec,'seed':seed,'stage':stage,
                 'numpy_version':np.__version__,'python_version':sys.version.split()[0],'data_seed':20261103,'train_bounds':[0,1024],'validation_bounds':[1024,1280],
                 'test_accessed':False,'lookback':16,'horizon':16,'fold':0,'origins':starts,'window_mse':losses,
                 'stored_numeric_values':count,'cap':300,'steps':steps,'curve':curve,
                 'training_data_sha256':hashlib.sha256(series[:1024].tobytes()).hexdigest(),
                 'files':{n:hashlib.sha256((out/n).read_bytes()).hexdigest() for n in ['checkpoint.npz','source.py']}}
        (out/'checkpoint.json').write_text(json.dumps(receipt,allow_nan=False,indent=2)+'\n')
        if not np.array_equal(forecast(out,series[starts[-1]-16:starts[-1]]),np.asarray(pred)):
            raise ValueError('checkpoint rollout parity failed')
    if stage=='s2':
        from ..checkpoint_store import write_inventory
        write_inventory(out)
    (out/'metrics.json').write_text(json.dumps(metric,allow_nan=False)+'\n')
    return metric


def forecast(bundle, context):
    import zipfile
    bundle=Path(bundle);receipt=json.loads((bundle/'checkpoint.json').read_text())
    if receipt.get('kind')!='evaluation_checkpoint':raise ValueError('unsupported checkpoint')
    for name in ('checkpoint.npz','source.py'):
        if hashlib.sha256((bundle/name).read_bytes()).hexdigest()!=receipt['files'][name]:
            raise ValueError('checkpoint checksum mismatch')
    with zipfile.ZipFile(bundle/'checkpoint.npz') as z:
        if sum(i.file_size for i in z.infolist())>65536:raise ValueError('checkpoint too large')
    with np.load(bundle/'checkpoint.npz',allow_pickle=False) as z: arrays={k:z[k] for k in z}
    width=receipt['spec']['width'];kind=receipt['spec']['kind']
    if kind not in ('mlp','ridge') or type(width) is not int or not 1<=width<=8:raise ValueError('invalid checkpoint spec')
    shapes={'mean':(16,),'scale':(16,),'target_mean':(),'target_scale':()}
    shapes.update({'w1':(16,width),'b1':(width,),'w2':(width,1),'b2':(1,)} if kind=='mlp' else {'coef':(17,1)})
    if set(arrays)!=set(shapes) or any(arrays[k].shape!=v for k,v in shapes.items()):raise ValueError('invalid checkpoint shape')
    if any(v.dtype.kind not in 'fi' or not np.isfinite(v).all() for v in arrays.values()):raise ValueError('invalid numeric arrays')
    if sum(v.size for v in arrays.values())>300 or np.any(arrays['scale']<=0) or arrays['target_scale']<=0:
        raise ValueError('invalid count or scale')
    context=np.asarray(context,dtype=float).copy()
    if context.shape!=(16,) or not np.isfinite(context).all():raise ValueError('expected 16 finite context values')
    result=[]
    for _ in range(16):
        x=((context-arrays['mean'])/arrays['scale'])[None]
        y=predict(arrays,x) if kind=='mlp' else np.column_stack([x,np.ones(1)])@arrays['coef']
        value=float(y[0,0]*arrays['target_scale']+arrays['target_mean']);result.append(value);context=np.r_[context[1:],value]
    return np.asarray(result)


def main():
    p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--out',required=True)
    p.add_argument('--stage',required=True);p.add_argument('--seed',type=int,default=101);p.add_argument('--attempt',type=int,default=1)
    a=p.parse_args();evaluate(json.loads(Path(a.spec).read_text()),a.stage,a.out,a.seed)

if __name__=='__main__':main()

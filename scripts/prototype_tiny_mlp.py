#!/usr/bin/env python3
"""Isolated synthetic training/export smoke. Not a registered gym or benchmark."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import numpy as np


def init(seed=101):
    rng = np.random.default_rng(seed)
    return {'w1': rng.normal(0, .25, (16, 8)), 'b1': np.zeros(8),
            'w2': rng.normal(0, .25, (8, 1)), 'b2': np.zeros(1)}


def predict(weights, x):
    return np.tanh(x @ weights['w1'] + weights['b1']) @ weights['w2'] + weights['b2']


def loss_grad(weights, x, y):
    h = np.tanh(x @ weights['w1'] + weights['b1'])
    err = h @ weights['w2'] + weights['b2'] - y
    dy = 2 * err / err.size
    dh = (dy @ weights['w2'].T) * (1 - h * h)
    return float(np.mean(err * err)), {'w1': x.T @ dh, 'b1': dh.sum(0),
                                     'w2': h.T @ dy, 'b2': dy.sum(0)}


def train(x, y, steps=600, seed=101):
    weights = init(seed); curve = []
    for step in range(steps):
        loss, grad = loss_grad(weights, x, y)
        if not np.isfinite(loss) or any(not np.isfinite(v).all() for v in grad.values()):
            raise ValueError('non-finite training state')
        if step % 20 == 0: curve.append({'step': step, 'train_mse': loss})
        for key in weights: weights[key] -= .02 * grad[key]
    curve.append({'step': steps, 'train_mse': loss_grad(weights, x, y)[0]})
    return weights, curve


def run(out):
    out = Path(out)
    if out.exists(): raise FileExistsError('use a fresh prototype output directory')
    rng = np.random.default_rng(20261103)
    x = rng.normal(size=(768, 16))
    y = (np.tanh(2*x[:, 0]) + .5*np.tanh(x[:, 1]-x[:, 2]))[:, None]
    train_x, val_x = x[:512], x[512:]
    train_y, val_y = y[:512], y[512:]
    mean, scale = train_x.mean(0), train_x.std(0) + 1e-8
    xx = (train_x-mean)/scale; vx = (val_x-mean)/scale
    weights, curve = train(xx, train_y)
    numeric_count = sum(v.size for v in weights.values()) + mean.size + scale.size
    if numeric_count > 300: raise ValueError('physical numeric cap exceeded')
    out.mkdir(parents=True)
    np.savez(out/'weights.npz', **weights, mean=mean, scale=scale)
    with np.load(out/'weights.npz', allow_pickle=False) as z:
        restored = predict({key:z[key] for key in weights}, (val_x-z['mean'])/z['scale'])
    assert np.array_equal(restored, predict(weights, vx)), 'reload prediction parity failed'
    # A train-only closed-form reference; this is a smoke, not an equal-compute comparison.
    design = np.column_stack([xx, np.ones(len(xx))])
    ridge = np.linalg.solve(design.T@design + 1e-3*np.eye(17), design.T@train_y)
    baseline = np.column_stack([vx, np.ones(len(vx))]) @ ridge
    shutil.copy2(__file__, out/'source.py')
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    report = {'schema':1, 'status':'synthetic_engineering_smoke', 'release_ready':False,
              'model':'16-8-1 tanh MLP', 'trainable_parameters':145,
              'stored_numeric_values':numeric_count, 'cap':300, 'steps':600,
              'seed':101, 'data_seed':20261103, 'split':'first 512 train, last 256 synthetic validation',
              'training_data_sha256':hashlib.sha256(train_x.tobytes()+train_y.tobytes()).hexdigest(),
              'validation_mse':float(np.mean((restored-val_y)**2)),
              'ridge_validation_mse':float(np.mean((baseline-val_y)**2)),
              'parity':'exact', 'curve':curve,
              'files':{n:sha(out/n) for n in ['weights.npz','source.py']},
              'limits':'No real data, time-series claim, search comparison or installed gym. Last iterate, no validation selection.'}
    (out/'manifest.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out', required=True)
    print(json.dumps(run(parser.parse_args().out), indent=2))

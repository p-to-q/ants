"""Read-only retained-model catalog. Recorded receipts are not live file checks."""
from __future__ import annotations

import math
import os
from pathlib import Path
import re

from .archive_index import _read
from .textsafe import loads_strict, clean_text

HEX = re.compile(r"[0-9a-f]{16,64}\Z")


def catalog_path():
    from .home import home
    explicit=os.environ.get('ANTS_MODEL_CATALOG')
    personal=home()/'models/catalog.json'
    return Path(explicit).expanduser() if explicit else personal if personal.exists() else Path('/var/lib/ants/models/catalog.json')


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def read_catalog(path=None):
    """Bounded metadata read only; never load weights, ledger, or a referenced path."""
    path = Path(path) if path is not None else catalog_path()
    try:
        data = loads_strict(_read(path).decode('utf-8'), max_chars=2 << 20)
        if not isinstance(data, dict) or data.get('schema') != 1:
            raise ValueError('unsupported saved-model catalog')
        rows = data.get('models')
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError('saved-model catalog must contain at most 1000 entries')
        seen, identity = set(), None
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('invalid saved-model record')
            if not isinstance(row.get('reason',''),str) or row.get('refit_status') is not None and not isinstance(row['refit_status'],str):
                raise ValueError('invalid model reason or refit status')
            c = row.get('candidate')
            if not isinstance(c, dict) or not isinstance(c.get('identity'), dict) or not isinstance(c.get('spec'), dict):
                raise ValueError('missing saved-model identity or specification')
            key = c.get('spec_key')
            if not isinstance(key, str) or not HEX.fullmatch(key) or key in seen:
                raise ValueError('invalid or duplicate model key')
            seen.add(key)
            ident = c['identity']
            scope = (ident.get('gym'), ident.get('epoch'), ident.get('contract_sha256'), c.get('metric'), c.get('direction'), c.get('stage'))
            if any(not isinstance(x, str) or not x for x in scope) or c['direction'] not in ('min','max'):
                raise ValueError('incomplete model evaluation scope')
            if ident['epoch'] != data.get('epoch') or (identity is not None and scope != identity):
                raise ValueError('saved models span incompatible evaluation scopes')
            identity = scope
            if not all(_number(c.get(k)) for k in ('value','n_params','cap')) or c['n_params'] < 0 or c['cap'] <= 0 or c['n_params'] > c['cap']:
                raise ValueError('invalid saved-model score or nominal cap')
            if row.get('refit') is not None:
                refit = row['refit']
                if not isinstance(refit, dict):
                    raise ValueError('invalid refit receipt')
                count = refit.get('stored_numeric_values')
                if count is not None and (not _number(count) or count < 0 or count > c['cap']):
                    raise ValueError('refit receipt exceeds its physical cap')
                evaluation=refit.get('validation')
                if evaluation is not None:
                    if not isinstance(evaluation,dict) or evaluation.get('kind')!='saved_refit_dev_validation' or evaluation.get('weights_sha256')!=refit.get('weights_sha256') or evaluation.get('dataset')!=refit.get('dataset') or evaluation.get('horizon')!=refit.get('horizon'):
                        raise ValueError('validation receipt differs from its weight variant')
                    metrics=evaluation.get('metrics',{});trace=evaluation.get('trace')
                    if not isinstance(metrics,dict) or not all(_number(metrics.get(k)) for k in ('mse_std','rel_mse')):
                        raise ValueError('invalid saved-weight validation metrics')
                    protocol=evaluation.get('protocol')
                    if not isinstance(protocol,dict) or not isinstance(protocol.get('evaluated_windows'),int) or protocol['evaluated_windows']<=0:
                        raise ValueError('missing validation sample count')
                    length=evaluation['horizon']
                    if not isinstance(length,int) or isinstance(length,bool) or not 1<=length<=720 or trace is not None and (not isinstance(trace,dict) or any(not isinstance(trace.get(k),list) or len(trace[k])!=length or not all(_number(v) for v in trace[k]) for k in ('steps','actual','forecast','seasonal_naive'))):
                        raise ValueError('invalid saved-weight forecast trace')

        return {**data, 'path':str(path), 'recorded_only':True}
    except FileNotFoundError:
        return {'schema':1,'models':[], 'path':str(path),'missing':True,'recorded_only':True}
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        return {'schema':1,'models':[], 'path':str(path),'error':clean_text(str(exc),200),'recorded_only':True}


def retention_label(row):
    reason = row.get('reason','')
    if reason.startswith('Best recorded development score'):
        return 'Cap leader'
    if 'representative of core' in reason:
        return 'Different route'
    return 'Worth a look'


def weight_label(row):
    # A cached assertion is deliberately not promoted to current file availability.
    status = row.get('refit_status')
    return {'verified_local':'Refit recorded','pending':'Refit pending','failed':'Refit failed'}.get(status,'Spec only')

"""Conditional, ledger-journaled Optuna proposals; scientific ledger is authoritative."""
from __future__ import annotations
import hashlib
import json
import math
import resource
import sys
from pathlib import Path
import time


class OptunaReference:
    name = 'tpe_reference'

    def __init__(self, c, seed=0, objective_stage=None):
        import optuna
        if optuna.__version__ != '5.0.0':
            raise ValueError('reference requires pinned Optuna 5.0.0')
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        self.o, self.c, self.seed, self.accept = optuna, c, seed, None
        self.stage=objective_stage or c.stages[0]
        if self.stage not in c.stages:raise ValueError("unknown objective fidelity")
        self.adapter_definition = {'schema':1, 'optuna':optuna.__version__,
            'n_startup_trials':10, 'multivariate':True, 'group':True, 'constant_liar':True,
            'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'history_limit':1500, 'journal':'ledger_intents_v1', 'objective_stage':self.stage}
        self.scope = hashlib.sha256(json.dumps({'contract':c.raw,'adapter':self.adapter_definition},sort_keys=True,default=str).encode()).hexdigest()
        self.order=[]; remaining=set(c.space.params)
        while remaining:
            ready=[n for n in c.space.params if n in remaining and set(c.space.params[n].when or {}) <= set(self.order)]
            if not ready: raise ValueError('cyclic or unknown conditional dependencies')
            self.order.extend(ready); remaining.difference_update(ready)

    def _sampler(self, ordinal):
        return self.o.samplers.TPESampler(seed=(self.seed+ordinal)%(2**32),
            n_startup_trials=10,multivariate=True,group=True,constant_liar=True)

    def _distribution(self,p):
        d=self.o.distributions
        if p.kind=='int':return d.IntDistribution(int(p.low),int(p.high))
        if p.kind in ('float','logfloat'):return d.FloatDistribution(p.low,p.high,log=p.kind=='logfloat')
        return d.CategoricalDistribution([True,False] if p.kind=='bool' else list(p.choices))

    def _active(self,spec):
        return self.c.space.canonical(spec)

    def _suggest(self,trial):
        spec={n:p.default_value() for n,p in self.c.space.params.items()}
        for n in self.order:
            if self.c.space.is_active(n,spec):
                p=self.c.space.params[n]
                if p.kind=='int':spec[n]=trial.suggest_int(n,int(p.low),int(p.high))
                elif p.kind in ('float','logfloat'):spec[n]=trial.suggest_float(n,p.low,p.high,log=p.kind=='logfloat')
                else:spec[n]=trial.suggest_categorical(n,[True,False] if p.kind=='bool' else list(p.choices))
        return self.c.space.validate(spec)

    def propose(self,ledger,arm,k,seen,**kw):
        from .proposers import Proposal, _hist
        h=_hist(ledger,self.c,arm,kw.get('hist'))
        if not h.epoch:raise ValueError('an explicit evaluation epoch is required')
        wall,cpu=time.perf_counter(),time.process_time()
        identity={'gym':self.c.gym,'arm':arm,'epoch':h.epoch,'scope':self.scope,'proposal_seed':self.seed}
        intents=[e['payload'] for e in ledger.events('optuna_intent')
                 if all(e['payload'].get(n)==v for n,v in identity.items())]
        rows=[r for r in ledger.trials(self.c.gym,arm) if r['epoch']==h.epoch ]
        queued={r['spec_key'] for r in rows};out=[]
        # An intent committed before a crash is reissued only if no evaluation was enqueued.
        for i in intents:
            if not i.get('accepted',True):continue
            spec=self.c.space.validate(i['spec']);key=self.c.space.key(spec)
            if key not in queued and key not in seen and (self.accept is None or self.accept(spec)):
                seen.add(key);out.append(Proposal(spec,self.name,note='recovered intent'))
                if len(out)==k:
                    ledger.event('optuna_overhead',{**identity,'wall_s':time.perf_counter()-wall,
                        'controller_cpu_s':time.process_time()-cpu,'recovered':len(out),
                        'maxrss_native':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                        'maxrss_unit':'bytes' if sys.platform=='darwin' else 'KiB'})
                    return out
        sampler=self._sampler(len(intents))
        study=self.o.create_study(direction='minimize' if self.c.direction=='min' else 'maximize',sampler=sampler)
        terminal=[r for r in rows if r['stage']==self.stage and r['state'] not in ('queued','running')][-1500:]
        finished_keys={r['spec_key'] for r in terminal}
        pending_by_key={r['spec_key']:r for r in rows if r['state'] in ('queued','running') and r['stage'] in (self.c.stages[0],self.stage) and r['spec_key'] not in finished_keys}
        pending=list(pending_by_key.values())
        if len(pending)>2000:raise ValueError('pending reference history exceeds bound')
        for r in terminal+pending:
            params=self._active(r['params']);dists={n:self._distribution(self.c.space.params[n]) for n in params}
            value=(r.get('metrics') or {}).get(self.c.primary)
            good=r['state']=='done' and isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
            state=self.o.trial.TrialState.RUNNING if r['state'] in ('queued','running') else self.o.trial.TrialState.COMPLETE if good else self.o.trial.TrialState.FAIL
            study.add_trial(self.o.trial.create_trial(params=params,distributions=dists,value=value if good else None,state=state))
        # Unsatisfied intents returned above are pending within this same proposal batch.
        virtual_keys=set()
        for intent in intents:
            if not intent.get('accepted',True) or intent['key'] in queued or intent['key'] in virtual_keys:continue
            virtual_keys.add(intent['key']);params=self._active(intent['spec'])
            study.add_trial(self.o.trial.create_trial(params=params,distributions={n:self._distribution(self.c.space.params[n]) for n in params},state=self.o.trial.TrialState.RUNNING))
        for _ in range(300*max(k,1)):
            if len(out)>=k:break
            study.sampler=self._sampler(len(intents))
            trial=study.ask();spec=self._suggest(trial);key=self.c.space.key(spec)
            accepted=key not in seen and (self.accept is None or self.accept(spec))
            ledger.event('optuna_intent',{**identity,'ordinal':len(intents),'spec':spec,'key':key,
                                         'trial_number':trial.number,'seed':self.seed,'accepted':accepted})
            intents.append({'key':key})
            if not accepted:
                study.tell(trial,state=self.o.trial.TrialState.FAIL);continue
            seen.add(key);out.append(Proposal(spec,self.name))
        ledger.event('optuna_overhead',{**identity,'wall_s':time.perf_counter()-wall,
            'controller_cpu_s':time.process_time()-cpu,'maxrss_native':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'maxrss_unit':'bytes' if sys.platform=='darwin' else 'KiB','terminal_used':len(terminal),'pending_used':len(pending),'returned':len(out)})
        return out

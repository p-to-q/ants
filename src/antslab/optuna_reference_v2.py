"""Optuna reference with domain-separated per-run/per-proposal random streams.

The original adapter remains available for exact historical identity checks.
"""
import hashlib
import json
from pathlib import Path

from .optuna_reference import OptunaReference


def sampler_seed(master_seed, ordinal):
    """Derive a stable uint32 without adjacent-run shifted-stream reuse."""
    payload = json.dumps(['ants-optuna-stream-v2', int(master_seed), int(ordinal)], separators=(',', ':')).encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], 'big')


class OptunaReferenceV2(OptunaReference):
    name = 'tpe_reference_v2'

    def __init__(self, c, seed=0, objective_stage=None):
        super().__init__(c, seed, objective_stage)
        self.adapter_definition = {
            **self.adapter_definition,
            'schema': 2,
            'stream_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'seed_derivation': 'sha256-json-domain-master-ordinal-first-u32be-v2',
        }
        self.scope = hashlib.sha256(json.dumps({'contract': c.raw, 'adapter': self.adapter_definition}, sort_keys=True, default=str).encode()).hexdigest()

    def _sampler(self, ordinal):
        return self.o.samplers.TPESampler(
            seed=sampler_seed(self.seed, ordinal), n_startup_trials=10,
            multivariate=True, group=True, constant_liar=True,
        )

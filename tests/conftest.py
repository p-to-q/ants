import sys
from pathlib import Path
import pytest

TOY = Path(__file__).resolve().parents[1] / "src/antslab/gyms/toy.py"

TOML = """
[task]
name="toy"
gym="toy"
description="minimise val_mse of a toy function"
code_paths=["{toy}"]
env_passthrough=["TOY_FAULT","TOY_FAULT_MOD","TOY_SLEEP"]
[budget]
trial_timeout_s={timeout}
max_attempts=2
unseal_limit=1
[objective]
primary="val_mse"
direction="min"
cap_metric="n_params"
caps=[100,300]
[cascade]
stages=["s0","s1","s2"]
promote_quantile=0.5
min_history=4
[commands]
eval=["{{python}}","-m","antslab.gyms.toy","--stage","{{stage}}","--spec","{{spec}}","--out","{{out}}","--seed","{{seed}}","--attempt","{{attempt}}"]
final=["{{python}}","-m","antslab.gyms.toy","--final","--stage","final","--spec","{{spec}}","--out","{{out}}","--seed","{{seed}}"]
[space.a]
type="float"
low=0.0
high=1.0
[space.b]
type="int"
low=1
high=30
[space.c]
type="cat"
choices=["u","v","w"]
"""


@pytest.fixture
def make_contract(tmp_path):
    def mk(timeout=20, toy=None):
        p = tmp_path / "task.toml"
        p.write_text(TOML.format(toy=toy or TOY, timeout=timeout))
        from antslab.contract import load_contract
        return load_contract(p)
    return mk


@pytest.fixture
def ledger(tmp_path):
    from antslab.ledger import Ledger
    return Ledger(tmp_path / "w" / "ledger.sqlite")

"""Audit follow-ups: relative paths, start-up cost, epoch stamping, strict JSON in the chain."""
import os
import subprocess

import pytest

from antslab.campaign import Campaign
from antslab.ledger import Ledger, canon
from antslab.proposers import make_proposer
from tests.test_chaos_common import CLI, write_contract


def test_relative_workdir_in_process(make_contract, tmp_path, monkeypatch):
    """The gym runs with cwd=<out dir>; a relative workdir used to hand it relative --spec/--out paths that resolve to nothing."""
    c = make_contract()
    monkeypatch.chdir(tmp_path)
    led = Ledger(tmp_path / "l" / "ledger.sqlite")
    cp = Campaign(led, c, "rel_w", "random", make_proposer("random", c, 0), 0, budget_evals=3)
    assert cp.workdir.is_absolute()
    assert cp.run(sleep=lambda s: None) == "budget"
    assert len([t for t in led.trials() if t["state"] == "done"]) == 3


def test_relative_workdir_and_contract_via_cli(tmp_path):
    write_contract(tmp_path)
    r = subprocess.run(CLI + ["run", "--contract", "task.toml", "--workdir", "rel_wd", "--arm", "random", "--max-evals", "3"],
                       cwd=tmp_path, capture_output=True, text=True, env=os.environ, timeout=120)
    assert r.returncode == 0, r.stderr
    led = Ledger(tmp_path / "rel_wd" / "ledger.sqlite")
    assert len([t for t in led.trials() if t["state"] == "done"]) == 3
    r = subprocess.run(CLI + ["status", "--contract", "task.toml", "--workdir", "rel_wd", "--arm", "random"],
                       cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_startup_does_not_load_every_trial(ledger, make_contract, tmp_path, monkeypatch):
    c = make_contract()
    cp = Campaign(ledger, c, tmp_path / "w", "random", make_proposer("random", c, 0), 0, budget_evals=2)
    cp.run(sleep=lambda s: None)
    n = len(ledger.trials("toy", "random"))
    monkeypatch.setattr(Ledger, "trials", lambda *a, **k: pytest.fail("Campaign start-up materialised every trial row"))
    cp2 = Campaign(ledger, c, tmp_path / "w", "random", make_proposer("random", c, 0), 0)
    assert cp2.n_proposed == n


def test_epoch_is_stamped_by_the_process_that_runs_the_trial(ledger, make_contract, tmp_path):
    """A trial enqueued under an old epoch but executed after an evaluator/data change was measured by the NEW machinery."""
    c = make_contract()
    ledger.enqueue("toy", "random", 1, {"a": 0.2, "b": 5, "c": "u"}, "t", spec_key="k1", epoch="old-epoch")
    cp = Campaign(ledger, c, tmp_path / "w", "random", make_proposer("random", c, 0), 0, budget_evals=1)
    assert cp.epoch != "old-epoch"
    cp.run(sleep=lambda s: None)
    assert ledger.get(1)["epoch"] == cp.epoch
    assert ledger.events("trial_finished")[0]["payload"]["epoch"] == cp.epoch


def test_canon_refuses_non_finite_numbers():
    with pytest.raises(ValueError):
        canon({"x": float("nan")})
    with pytest.raises(ValueError):
        canon({"x": [float("inf")]})
    assert canon({"b": 1, "a": 2.5}) == '{"a":2.5,"b":1}'


def test_non_finite_payload_never_reaches_the_chain(ledger):
    with pytest.raises(ValueError):
        ledger.event("x", {"v": float("nan")})
    assert ledger.events() == [] and ledger.verify_chain()[0]

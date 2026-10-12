"""kill -9 the daemon at random moments, 30+ times; then audit the ledger like an adversary."""
import json
import os
import random
import signal
import subprocess
import time

import pytest

from antslab.ledger import FINAL_STATES, Ledger
from tests.test_chaos_common import run_cmd, write_contract


@pytest.mark.slow
@pytest.mark.parametrize("seed", [1, 2])
def test_kill9_never_duplicates_or_loses_evidence(tmp_path, seed):
    contract, wd = write_contract(tmp_path), tmp_path / "wd"
    env = {**os.environ, "TOY_SLEEP": "0.1"}
    rnd = random.Random(seed)
    kills, finished = 0, False
    for _ in range(150):
        p = subprocess.Popen(run_cmd(contract, wd, arm="evo", seed=3 + seed, max_evals=180), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            out, _ = p.communicate(timeout=rnd.uniform(0.4, 1.8))
            assert p.returncode == 0, out
            finished = True
            break
        except subprocess.TimeoutExpired:
            p.send_signal(signal.SIGKILL)
            p.communicate()
            kills += 1
    assert finished, "never finished"
    assert kills >= 30, kills

    l = Ledger(wd / "ledger.sqlite")
    ok, bad = l.verify_chain()
    assert ok, bad
    rows = l.trials("toy", "evo")
    assert not [t for t in rows if t["state"] in ("running",)], "a row is stuck in 'running'"

    done = [t for t in rows if t["state"] == "done"]
    assert len(done) >= 100
    # 1. no (spec, stage) was evaluated twice within an epoch
    keys = [(t["epoch"], t["spec_key"], t["stage"]) for t in done]
    assert len(keys) == len(set(keys)), "a spec was evaluated twice at the same stage"
    every = [(t["epoch"], t["spec_key"], t["stage"]) for t in rows if t["state"] in ("done", "queued", "running")]
    assert len(every) == len(set(every)), "a spec was enqueued twice at the same stage"

    # 2. every 'done' row has exactly one trial_finished event that agrees with it
    fin = {}
    for e in l.events("trial_finished"):
        pl = e["payload"]
        assert pl["id"] not in fin, f"trial {pl['id']} has two trial_finished events"
        fin[pl["id"]] = pl
    for t in done:
        pl = fin.get(t["id"])
        assert pl is not None, f"done trial {t['id']} has no trial_finished event"
        assert pl["state"] == "done" and pl["attempt"] == t["attempt"] and pl["metrics"] == t["metrics"]
        assert pl["spec_key"] == t["spec_key"] and pl["stage"] == t["stage"] and pl["epoch"] == t["epoch"]
    extra = set(fin) - {t["id"] for t in rows if t["state"] in FINAL_STATES}
    assert not extra, f"trial_finished events for trials that are not final: {extra}"
    crashed = {t["id"] for t in rows if t["state"] == "crashed"}
    lost = {e["payload"]["id"] for e in l.events("trial_crashed")}
    assert crashed <= lost | set(fin), "a crashed row has no event explaining it"

    # 3. the kills really did hit running trials
    assert any(e["kind"] in ("recovered", "trial_reclaimed") for e in l.events())

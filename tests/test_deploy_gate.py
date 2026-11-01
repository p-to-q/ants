"""A failed/interrupted install must never enter onboarding or import replaced code."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

GATE=Path(__file__).resolve().parents[1]/'deploy/ants-deploy-gate.py'
spec=importlib.util.spec_from_file_location('deploy_gate_under_test',GATE)
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)


@pytest.mark.parametrize('phase',['preparing','installing','verifying','failed'])
def test_guard_blocks_unready_install_without_importing_ants(tmp_path,phase):
    path=tmp_path/'state.json';gate.write(path,phase,'0.6.4')
    p=subprocess.run([sys.executable,'-I',str(GATE),'check','--state',str(path)],capture_output=True,text=True)
    assert p.returncode==75 and 'No onboarding' in p.stdout
    assert phase in p.stdout and '0.6.4' in p.stdout


def test_only_explicit_ready_clears_gate_and_status_is_json(tmp_path):
    path=tmp_path/'state.json'
    gate.write(path,'preparing','0.6.4');attempt=gate.read(path)['attempt']
    gate.write(path,'failed',reason='test install failed')
    assert gate.main(['check','--state',str(path)])==75
    gate.write(path,'ready')
    assert gate.main(['check','--state',str(path)])==0
    assert gate.read(path)['attempt']==attempt and 'reason' not in gate.read(path)
    history=[json.loads(s)['phase'] for s in (tmp_path/'history.jsonl').read_text().splitlines()]
    assert history==['preparing','failed','ready']


def test_invalid_and_symlink_state_fail_closed_but_legacy_is_explicit(tmp_path):
    path=tmp_path/'state.json'
    assert gate.read(path)['phase']=='unrecorded'
    path.write_text('{partial json')
    assert gate.main(['check','--state',str(path)])==75
    link=tmp_path/'link';link.symlink_to(path)
    assert gate.main(['check','--state',str(link)])==75

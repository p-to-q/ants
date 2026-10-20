"""CLI details from the audit: spreadsheet formula injection in export, loopback-only board server, --version."""
import csv
import subprocess
import sys

from antslab.cli import _cell


def test_csv_cells_that_look_like_formulas_are_neutralised():
    assert _cell("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert _cell("+1+1") == "'+1+1" and _cell("-2") == "'-2" and _cell("@SUM(A1)") == "'@SUM(A1)"
    assert _cell(-2.5) == -2.5 and _cell("ok") == "ok" and _cell(None) is None


def test_cli_prints_a_version():
    r = subprocess.run([sys.executable, "-m", "antslab.cli", "--version"], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.startswith("ants ")

def test_status_respects_json_flag(ledger, make_contract, capsys):
    """AGENTS.md: every read command has --json. `ants status` used to always print
    JSON and silently ignore --json; now --json selects JSON and the default is text."""
    import json as j
    import argparse
    from antslab import cli
    c = make_contract()
    a = argparse.Namespace(contract=None, workdir=None, arm="h", json=False)
    # text mode (default)
    cli.cmd_status(a)
    out = capsys.readouterr().out
    assert "chain" in out and "disk" in out and not out.lstrip().startswith("{")
    # json mode
    a.json = True
    cli.cmd_status(a)
    out = capsys.readouterr().out
    doc = j.loads(out)
    assert {"counts", "free_gb", "disk_level", "chain_ok", "head", "unseals"} <= set(doc)


def test_data_gains_json_flag(make_contract, capsys, tmp_path, monkeypatch):
    """`ants data` used to have no --json at all; every read command must accept it."""
    import json as j
    import argparse
    from antslab import cli
    a = argparse.Namespace(action="list", catalog=None, tier="dev", names=None,
                           present=False, all=True, partial=False, json=True)
    # list --json: stable document, no header lines
    cli.cmd_data(a)
    out = capsys.readouterr().out
    doc = j.loads(out)
    assert {"roles", "synthetic_entries"} <= set(doc)
    # verify --json
    a2 = argparse.Namespace(action="verify", catalog=None, tier="dev", names=None,
                            present=False, all=True, partial=False, json=True)
    cli.cmd_data(a2)
    doc2 = j.loads(capsys.readouterr().out)
    assert {"ok", "problems"} <= set(doc2)

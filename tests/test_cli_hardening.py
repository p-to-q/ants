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

def test_status_respects_json_flag(ledger, make_contract, capsys, monkeypatch, tmp_path):
    """AGENTS.md: every read command has --json. `ants status` used to always print
    JSON and silently ignore --json; now --json selects JSON and the default is text."""
    import json as j
    import argparse
    from antslab import cli
    c = make_contract()
    monkeypatch.setattr(cli, "_open", lambda a: (tmp_path, ledger, c))
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
    monkeypatch.setattr(ledger, "verify_chain", lambda: (False, 7))
    assert cli.cmd_status(a) == 2
    assert j.loads(capsys.readouterr().out)["chain_bad_seq"] == 7


def test_data_gains_json_flag(make_contract, capsys, tmp_path, monkeypatch):
    """`ants data` used to have no --json at all; every read command must accept it."""
    import json as j
    import argparse
    from antslab import cli
    catalog = tmp_path / "catalog.toml"
    catalog.write_text('[dataset.example]\nfamily="toy"\nrole="dev"\nraw="raw/missing.csv"\nformat="ett_csv"\n')
    (tmp_path / "MANIFEST.sha256").write_text("0" * 64 + "  raw/missing.csv\n")
    a = argparse.Namespace(action="list", catalog=str(catalog), tier="dev", names=None,
                           present=False, all=True, partial=False, json=True)
    # list --json: stable document, no header lines
    cli.cmd_data(a)
    out = capsys.readouterr().out
    doc = j.loads(out)
    assert {"roles", "synthetic_entries"} <= set(doc)
    # verify --json
    a2 = argparse.Namespace(action="verify", catalog=str(catalog), tier="dev", names=None,
                            present=False, all=True, partial=False, json=True)
    assert cli.cmd_data(a2) == 2
    doc2 = j.loads(capsys.readouterr().out)
    assert {"ok", "problems"} <= set(doc2)
    assert doc2["ok"] is False and doc2["problems"]


def test_data_build_and_fetch_json_are_isolated(tmp_path, monkeypatch, capsys):
    import argparse
    import json
    from antslab import cli, data, fetch
    catalog = tmp_path / "catalog.toml"
    catalog.write_text("[dataset]\n")
    (tmp_path / "sources.toml").write_text("")
    monkeypatch.setattr(data.Catalog, "build_processed", lambda self, names: ["example"])
    report = {k: [] for k in ("fetched", "have", "failed", "mismatch", "unpinned")}
    report["failed"] = ["example"]
    monkeypatch.setattr(fetch, "fetch", lambda base, tier: report)
    args = argparse.Namespace(action="build", catalog=str(catalog), tier="dev", names=None,
                              present=False, all=False, partial=False, json=True)
    assert cli.cmd_data(args) == 0
    assert json.loads(capsys.readouterr().out)["built"] == 1
    args.action = "fetch"
    assert cli.cmd_data(args) == 2
    assert json.loads(capsys.readouterr().out)["failed"] == 1


def test_data_json_flag_is_accepted_by_parser(monkeypatch, capsys):
    from antslab import cli
    monkeypatch.setattr(cli, "cmd_data", lambda a: print('{"parsed": true}') or 0)
    assert cli.main(["data", "list", "--json"]) == 0
    assert '"parsed": true' in capsys.readouterr().out

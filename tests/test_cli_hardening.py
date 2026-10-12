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

"""Security tests for the maintainer patch gate (LLM-drafted patches are hostile input).

Every case that is meant to be rejected is first checked to be *valid Python* after the edit, so a rejection can never be
explained by a mere syntax error: the gate has to see the dangerous construct itself.
"""
import ast
import os
import sys
import time
from pathlib import Path

import pytest

from antslab.maintainer import Edit, Maintainer, Patch, Policy, _run, check_patch

GYM = '''import json
import math
import os

LIMIT = 10
LOG = "log.txt"


def evaluate(spec):
    return {"val": 10.0 / spec["x"], "n": 3}


def _dump(path, text):
    with open(path, "w") as f:
        f.write(text)


def _log(text):
    _dump(LOG, text)
'''
GYM_PATH = "src/antslab/gyms/g.py"
PYTEST = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]
TEST = '''import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from antslab.gyms.g import evaluate
def test_basic():
    assert evaluate({"x": 2})["val"] == 5.0
'''
KW = dict(module="antslab.gyms.g", func="evaluate", specs=[{"x": 1}, {"x": 2}, {"x": 5}], repair_specs=[{"x": 0}])


@pytest.fixture
def proj(tmp_path):
    r = tmp_path / "proj"
    (r / "src/antslab/gyms").mkdir(parents=True)
    (r / "tests").mkdir()
    (r / "src/antslab/__init__.py").write_text("")
    (r / "src/antslab/gyms/__init__.py").write_text("X = 1\n")
    (r / GYM_PATH).write_text(GYM)
    (r / "src/antslab/ledger.py").write_text("# protected\nx = 1\n")
    (r / "tests/test_g.py").write_text(TEST)
    return r


def edit(old, new, path=GYM_PATH):
    return Patch([Edit(path, old, new)], "r", "A")


def reasons(proj, old, new, path=GYM_PATH):
    return check_patch(edit(old, new, path), proj, Policy())


def assert_valid_python_after(proj, old, new):
    ast.parse((proj / GYM_PATH).read_text().replace(old, new, 1))


# --------------------------------------------------------------------------- AST gate: bypasses of the old token deny-list
BYPASSES = {
    "getattr+concat": ('"n": 3', '"n": getattr(__builtins__, "op" + "en") and 3'),
    "dunder import": ('"n": 3', '"n": __import__("o" + "s").getcwd() and 3'),
    "importlib": ("import json", "import json\nimport importlib"),
    "compile": ('"n": 3', '"n": compile("1", "x", "eval") and 3'),
    "class bases subclasses": ('"n": 3', '"n": ().__class__.__bases__[0].__subclasses__() and 3'),
    "globals subscript": ('"n": 3', '"n": globals()["__builtins__"] and 3'),
    "vars": ('"n": 3', '"n": vars() and 3'),
    "space before paren eval": ('"n": 3', '"n": eval ("1+2")'),
    "space before paren open": ('"n": 3', '"n": open ("/etc/passwd") and 3'),
    "alias then call": ("LIMIT = 10", "LIMIT = 10\nop = open"),
    "fullwidth unicode identifier": ('"n": 3', '"n": ｏｐｅｎ("/etc/passwd") and 3'),
    "new import socket": ("import json", "import json\nimport socket"),
    "import socket on a changed line": ("LIMIT = 10", "LIMIT = 10; import socket"),
    "from os import system": ("import json", "import json\nfrom os import system as run_it"),
    "relative import of the ledger": ("import json", "import json\nfrom ..ledger import Ledger"),
    "relative import of the maintainer": ("import json", "import json\nfrom .. import maintainer"),
    "from shutil import rmtree": ("import json", "import json\nfrom shutil import rmtree"),
    "import subprocess alias": ("import json", "import json\nimport subprocess as sp"),
    "import pickle": ("import json", "import json\nimport pickle"),
    "import ctypes": ("import json", "import json\nimport ctypes"),
    "relative import of the evaluator": ("import json", "import json\nfrom .. import runner"),
    "Path.read_text": ('"n": 3', '"n": len(__import__("pathlib").Path("/etc/passwd").read_text())'),
    "pathlib read_text": ("import json", "import json\nfrom pathlib import Path\nSECRET = Path('/etc/passwd').read_text()"),
    "os.environ read": ('"n": 3', '"n": len(os.environ)'),
    "getattr on os": ('"n": 3', '"n": getattr(os, "sys" + "tem")("true")'),
    "format string attribute walk": ('"n": 3', '"n": "{0.__class__.__init__.__globals__}".format(1)'),
    "__builtins__ name": ('"n": 3', '"n": __builtins__ and 3'),
    "__dict__ access": ('"n": 3', '"n": os.__dict__ and 3'),
    "setattr": ("LIMIT = 10", "LIMIT = 10\nsetattr(math, 'pi', 3)"),
    "breakpoint": ('"n": 3', '"n": breakpoint() or 3'),
    "input": ('"n": 3', '"n": input() and 3'),
    "exec in lambda": ("LIMIT = 10", "LIMIT = 10\nF = lambda s: exec(s)"),
}


@pytest.mark.parametrize("name", sorted(BYPASSES))
def test_dangerous_constructs_are_rejected_even_when_the_raw_token_list_misses_them(proj, name):
    old, new = BYPASSES[name]
    assert_valid_python_after(proj, old, new)
    bad = reasons(proj, old, new)
    assert bad, f"{name}: dangerous patch was accepted"
    assert not any("does not parse" in b for b in bad)


def test_changing_the_arguments_of_an_existing_dangerous_call_is_rejected(proj):
    # 'open(' already occurs in the old text, so a raw "token not in old" rule lets this through
    old, new = 'open(path, "w")', 'open("/etc/cron.d/x", "w")'
    assert_valid_python_after(proj, old, new)
    assert reasons(proj, old, new)


def test_adding_a_call_to_existing_dangerous_code_is_rejected(proj):
    old, new = '    return {"val": 10.0 / spec["x"], "n": 3}', '    _dump("/tmp/x", "y")\n    return {"val": 10.0 / spec["x"], "n": 3}'
    assert_valid_python_after(proj, old, new)
    assert any("dangerous" in b for b in reasons(proj, old, new))


def test_redirecting_the_path_constant_used_by_dangerous_code_is_rejected(proj):
    old, new = 'LOG = "log.txt"', 'LOG = "/etc/passwd"'
    assert_valid_python_after(proj, old, new)
    assert reasons(proj, old, new)


def test_functions_that_perform_io_are_frozen(proj):
    old, new = "def _dump(path, text):", 'def _dump(path, text):\n    """now documented."""'
    assert_valid_python_after(proj, old, new)
    assert reasons(proj, old, new)


def test_a_syntactically_broken_result_is_rejected_by_the_static_gate(proj):
    bad = reasons(proj, '"n": 3', '"n": 3 +')
    assert any("does not parse" in b for b in bad)


@pytest.mark.parametrize("old,new", [
    ('"val": 10.0 / spec["x"]', '"val": 10.0 / spec["x"] if spec["x"] else 0.0'),
    ('"n": 3', '"n": 4'),
    ("LIMIT = 10", "LIMIT = 11"),
    ("import json", "import json\nfrom ..data import Catalog\nfrom . import toy"),
    ("import math", "import math\nimport numpy as np\nfrom collections import Counter\nimport statistics"),
    ('    return {"val": 10.0 / spec["x"], "n": 3}',
     '    s = math.sqrt(abs(spec["x"]) + 1.0)\n    return {"val": 10.0 / spec["x"] / s * s, "n": 3}'),
])
def test_ordinary_repairs_still_pass(proj, old, new):
    assert reasons(proj, old, new) == []


def test_preexisting_dangerous_code_is_not_a_reason_to_reject_unrelated_edits(proj):
    assert reasons(proj, '"n": 3', '"n": 5') == []


# --------------------------------------------------------------------------- paths
def test_path_traversal_and_odd_paths_are_rejected_without_raising(proj, tmp_path):
    (tmp_path / "outside.py").write_text("a = 1\n")
    paths = ["../outside.py", "../../outside.py", "/etc/passwd", str(tmp_path / "outside.py"),
             "src/antslab/gyms/../ledger.py", "src/antslab/gyms/../../../outside.py", "src/antslab/gyms/../../../../etc/passwd",
             "", ".", "src/antslab/gyms", "src/antslab/gyms/", "src/antslab/gyms/g.py\x00.txt", "src/antslab/gyms/" + "a" * 5000 + ".py",
             "src//antslab///gyms/../ledger.py", "src/antslab/gyms/%2e%2e/ledger.py", "~/x.py", "src\\antslab\\gyms\\g.py"]
    for p in paths:
        bad = check_patch(Patch([Edit(p, "a", "b")]), proj, Policy())
        assert bad, f"path {p!r} was accepted"


def test_case_variants_of_protected_and_allowed_paths_are_rejected(proj):
    for p in ["src/antslab/Ledger.py", "SRC/antslab/ledger.py", "src/antslab/LEDGER.PY", "Tests/test_g.py", "TESTS/test_g.py",
              "src/antslab/Gyms/g.py", "src/antslab/gyms/G.py", "src/antslab/gyms/../Ledger.py"]:
        assert check_patch(Patch([Edit(p, "x", "y")]), proj, Policy()), p


def test_allow_glob_does_not_cross_directory_boundaries(proj):
    sub = proj / "src/antslab/gyms/sub"
    sub.mkdir()
    (sub / "deep.py").write_text("A = 1\n")
    bad = check_patch(Patch([Edit("src/antslab/gyms/sub/deep.py", "A = 1", "A = 2")]), proj, Policy())
    assert any("allow list" in b for b in bad)


def test_symlink_inside_allowed_dir_pointing_outside_the_repo_is_rejected(proj, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = 1\n")
    os.symlink(outside, proj / "src/antslab/gyms/evil.py")
    assert check_patch(Patch([Edit("src/antslab/gyms/evil.py", "SECRET = 1", "SECRET = 2")]), proj, Policy())


def test_symlink_inside_allowed_dir_pointing_at_a_protected_file_is_rejected(proj):
    os.symlink(proj / "src/antslab/ledger.py", proj / "src/antslab/gyms/ledger_alias.py")
    assert check_patch(Patch([Edit("src/antslab/gyms/ledger_alias.py", "x = 1", "x = 2")]), proj, Policy())


def test_symlink_to_another_allowed_file_is_rejected_too(proj):
    (proj / "src/antslab/gyms/h.py").write_text("H = 1\n")
    os.symlink(proj / "src/antslab/gyms/h.py", proj / "src/antslab/gyms/alias.py")
    assert check_patch(Patch([Edit("src/antslab/gyms/alias.py", "H = 1", "H = 2")]), proj, Policy())


def test_symlinked_directory_component_is_rejected(proj, tmp_path):
    d = tmp_path / "elsewhere"
    d.mkdir()
    (d / "x.py").write_text("A = 1\n")
    os.symlink(d, proj / "src/antslab/gyms/linkdir")
    assert check_patch(Patch([Edit("src/antslab/gyms/linkdir/x.py", "A = 1", "A = 2")]), proj, Policy())


def test_patches_cannot_create_files(proj):
    bad = check_patch(Patch([Edit("src/antslab/gyms/new.py", "", "import os\n")]), proj, Policy())
    assert bad and not (proj / "src/antslab/gyms/new.py").exists()


# --------------------------------------------------------------------------- sizes and encodings
def test_huge_old_or_new_text_is_rejected_quickly(proj):
    t = time.time()
    huge = "x = 1  # " + "a" * 5_000_000
    assert check_patch(edit("LIMIT = 10", huge), proj, Policy())
    assert check_patch(edit(huge, "LIMIT = 10"), proj, Policy())
    assert time.time() - t < 5


def test_too_many_edits_are_rejected(proj):
    many = Patch([Edit(GYM_PATH, f"LIMIT = {i}", f"LIMIT = {i + 1}") for i in range(500)])
    assert any("edits" in b for b in check_patch(many, proj, Policy()))


def test_binary_or_undecodable_target_is_rejected_not_raised(proj):
    (proj / "src/antslab/gyms/bin.py").write_bytes(b"A = 1\n\x00\xff\xfe\n")
    assert check_patch(Patch([Edit("src/antslab/gyms/bin.py", "A = 1", "A = 2")]), proj, Policy())


@pytest.mark.parametrize("new", ["LIMIT = 10\x00", "LIMIT = '\ud800'", "LIMIT = 10\x1b[2J"])
def test_nul_lone_surrogates_and_escape_bytes_in_the_patch_are_rejected(proj, new):
    assert check_patch(edit("LIMIT = 10", new), proj, Policy())


def test_crlf_files_roundtrip_byte_for_byte_through_apply_and_rollback(proj, ledger, tmp_path):
    raw = GYM.replace("\n", "\r\n").encode()
    (proj / GYM_PATH).write_bytes(raw)
    p = Patch([Edit(GYM_PATH, '"val": 10.0 / spec["x"]', '"val": 10.0 / spec["x"] if spec["x"] else 0.0')], "guard", "A")
    m = Maintainer(ledger, proj, tmp_path / "bk", test_cmd=PYTEST)
    r = m.attempt(p, **KW)
    assert r["applied"], r
    after = (proj / GYM_PATH).read_bytes()
    assert b"\r\n" in after and b"if spec" in after and b"\n" not in after.replace(b"\r\n", b"")      # no mixed line endings
    m.rollback(r["id"])
    assert (proj / GYM_PATH).read_bytes() == raw


def test_rollback_of_an_init_file_restores_the_right_path(proj, ledger, tmp_path):
    # file names are derived from the path: '__init__.py' contains the '__' that used to stand for '/'
    p = Patch([Edit("src/antslab/gyms/__init__.py", "X = 1", "X = 2")], "bump", "A")
    m = Maintainer(ledger, proj, tmp_path / "bk", test_cmd=PYTEST)
    r = m.attempt(p, **{**KW, "repair_specs": [{"x": 1}]})
    assert r["applied"], r
    assert (proj / "src/antslab/gyms/__init__.py").read_text() == "X = 2\n"
    assert m.rollback(r["id"]) == ["src/antslab/gyms/__init__.py"]
    assert (proj / "src/antslab/gyms/__init__.py").read_text() == "X = 1\n"
    assert not (proj / "src/antslab/gyms/init").exists()


def test_rollback_refuses_ids_that_are_not_patch_digests(proj, ledger, tmp_path):
    m = Maintainer(ledger, proj, tmp_path / "bk")
    (tmp_path / "bk").mkdir()
    for pid in ["../..", "../proj", "/etc", "a/b", "", ".."]:
        with pytest.raises(ValueError):
            m.rollback(pid)


def test_edits_are_checked_in_application_order(proj):
    # the first edit makes the second 'old' ambiguous: checking each edit against the original text would miss that
    p = Patch([Edit(GYM_PATH, "LIMIT = 10", "LIMIT = 10\nOTHER = 10"), Edit(GYM_PATH, "= 10", "= 11")])
    assert any("exactly once" in b for b in check_patch(p, proj, Policy()))


def test_atomic_failure_leaves_tree_untouched(proj, ledger, tmp_path):
    before = (proj / GYM_PATH).read_bytes()
    m = Maintainer(ledger, proj, tmp_path / "bk", test_cmd=PYTEST)
    r = m.attempt(edit('"n": 3', '"n": getattr(__builtins__, "op" + "en") and 3'), **KW)
    assert not r["applied"] and (proj / GYM_PATH).read_bytes() == before


# --------------------------------------------------------------------------- the sandbox is not a trusted environment
def test_sandbox_processes_do_not_inherit_secrets_from_the_environment(proj, ledger, tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_X_KEY", "sk-" + "live-supersecret")
    probe = [sys.executable, "-c", "import os,sys; sys.exit(1 if any('supersecret' in v for v in os.environ.values()) else 0)"]
    m = Maintainer(ledger, proj, tmp_path / "bk", test_cmd=probe)
    fix = Patch([Edit(GYM_PATH, '"val": 10.0 / spec["x"]', '"val": 10.0 / spec["x"] if spec["x"] else 0.0')], "guard", "A")
    r = m.attempt(fix, **KW)
    assert r["applied"], r


def test_a_hanging_sandbox_command_times_out_instead_of_raising_and_kills_children(tmp_path):
    marker = tmp_path / "alive"
    (tmp_path / "child.py").write_text("import sys, time, pathlib\ntime.sleep(3)\npathlib.Path(sys.argv[1]).write_text('x')\n")
    (tmp_path / "parent.py").write_text("import subprocess, sys, time\nsubprocess.Popen([sys.executable, 'child.py', sys.argv[1]])\ntime.sleep(60)\n")
    t = time.time()
    rc, out = _run([sys.executable, "parent.py", str(marker)], tmp_path, 1)
    assert rc != 0 and "timeout" in out.lower() and time.time() - t < 10
    time.sleep(4)
    assert not marker.exists(), "grandchild survived the timeout"


# --------------------------------------------------------------------------- the golden replay
def test_a_patch_that_drops_or_corrupts_a_metric_is_drift(proj, ledger, tmp_path):
    for old, new in [(', "n": 3}', "}"), (', "n": 3}', ', "n": float("nan")}')]:
        m = Maintainer(ledger, proj, tmp_path / "bk", test_cmd=[sys.executable, "-c", "pass"])
        r = m.attempt(edit(old, new), **{**KW, "repair_specs": [{"x": 1}]})
        assert not r["applied"] and any("drifted" in x for x in r["reasons"]), r


def test_a_patched_module_cannot_forge_the_golden_result(proj, tmp_path):
    # called directly (bypassing the static gate): an atexit hook that prints a second 'result' must not be believed
    import shutil
    from antslab.maintainer import golden
    patched = tmp_path / "patched"
    shutil.copytree(proj, patched)
    forged = ('import atexit\natexit.register(lambda: print(\'[{"ok": true, "m": {"val": 10.0, "n": 3}}, {"ok": true, "m": {"val": 5.0, "n": 3}},'
              ' {"ok": true, "m": {"val": 2.0, "n": 3}}, {"ok": true, "m": {"val": 1.0, "n": 3}}]\'))\n')
    g = patched / GYM_PATH
    g.write_text(forged + g.read_text().replace('"n": 3', '"n": 4'))
    bad = golden(proj, patched, specs=KW["specs"], repair_specs=[{"x": 1}], module=KW["module"], func=KW["func"])
    assert any("drifted" in b for b in bad), bad


# --------------------------------------------------------------------------- the real gyms stay repairable, except their I/O code
REAL = Path(__file__).resolve().parents[1] / "src/antslab/gyms/tinyts.py"


def test_real_gym_compute_code_is_patchable_but_its_io_code_is_frozen():
    from antslab.maintainer import ast_gate
    src = REAL.read_text()
    assert ast_gate(src, src.replace("A[np.diag_indices_from(A)] += (alpha + alpha0) * sc", "A[np.diag_indices_from(A)] += (alpha + alpha0) * sc * 1.0", 1)) == []
    assert ast_gate(src, src.replace("(series - mu) / sd", "(series - mu) / (sd + 1e-12)", 1)) == []
    assert ast_gate(src, src.replace('Path(a.out, "metrics.json").write_text(json.dumps(m))', 'Path("/tmp/x").write_text(json.dumps(m))', 1))
    assert ast_gate(src, src.replace("os').environ.get('ANTS_MAX_WINDOW_ELEMS'", "os').environ.get('HOME'", 1))
    assert ast_gate(src, src.replace("import json\n", "import json\nimport socket\n", 1))

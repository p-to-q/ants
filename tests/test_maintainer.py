import sys
from pathlib import Path
import pytest
from antslab.maintainer import Edit, Maintainer, Patch, Policy, check_patch, draft_repair

GYM = '''def evaluate(spec):
    return {"val": 10.0 / spec["x"], "n": 3}
'''
TEST = '''import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from antslab.gyms.g import evaluate
def test_basic():
    assert evaluate({"x": 2})["val"] == 5.0
'''


@pytest.fixture
def proj(tmp_path):
    r = tmp_path / "proj"
    (r / "src/antslab/gyms").mkdir(parents=True); (r / "tests").mkdir()
    for d in ("src/antslab", "src/antslab/gyms"):
        (r / d / "__init__.py").write_text("")
    (r / "src/antslab/gyms/g.py").write_text(GYM)
    (r / "src/antslab/ledger.py").write_text("# protected\nx = 1\n")
    (r / "tests/test_g.py").write_text(TEST)
    return r


FIX = Patch([Edit("src/antslab/gyms/g.py", '"val": 10.0 / spec["x"]', '"val": 10.0 / spec["x"] if spec["x"] else 0.0')], "guard zero", "A")
KW = dict(module="antslab.gyms.g", func="evaluate", specs=[{"x": 1}, {"x": 2}, {"x": 5}], repair_specs=[{"x": 0}])


def test_policy_blocks_protected_oversized_and_ambiguous_patches(proj):
    pol = Policy()
    assert any("protected" in b for b in check_patch(Patch([Edit("src/antslab/ledger.py", "x = 1", "x = 2")]), proj, pol))
    assert any("protected" in b for b in check_patch(Patch([Edit("tests/test_g.py", "assert", "pass #")]), proj, pol))
    (proj / "src/antslab/other.py").write_text("y = 1\n")
    assert any("allow list" in b for b in check_patch(Patch([Edit("src/antslab/other.py", "y = 1", "y = 2")]), proj, pol))
    assert any("escapes" in b for b in check_patch(Patch([Edit("../outside.py", "a", "b")]), proj, pol))
    assert any("exactly once" in b for b in check_patch(Patch([Edit("src/antslab/gyms/g.py", "spec", "s")]), proj, pol))
    big = Patch([Edit("src/antslab/gyms/g.py", GYM, "\n".join("a = 1" for _ in range(60)))])
    assert any("lines" in b for b in check_patch(big, proj, pol))
    evil = Patch([Edit("src/antslab/gyms/g.py", '"n": 3', '"n": __import__("os").system("x")')])
    assert any("forbidden" in b for b in check_patch(evil, proj, pol))
    assert check_patch(FIX, proj, pol) == []


def test_good_repair_is_applied_logged_and_rolled_back_exactly(proj, ledger, tmp_path):
    m = Maintainer(ledger, proj, tmp_path / "bk", test_cmd=[sys.executable, "-m", "pytest", "-q", "-x"])
    before = (proj / "src/antslab/gyms/g.py").read_text()
    r = m.attempt(FIX, **KW)
    assert r["applied"], r
    assert "if spec" in (proj / "src/antslab/gyms/g.py").read_text()
    assert [e["kind"] for e in ledger.events() if e["kind"].startswith("patch_")] == ["patch_proposed", "patch_applied"]
    m.rollback(r["id"])
    assert (proj / "src/antslab/gyms/g.py").read_text() == before
    assert ledger.verify_chain()[0]


def test_patch_that_changes_a_correct_metric_is_rejected_and_tree_untouched(proj, ledger, tmp_path):
    drift = Patch([Edit("src/antslab/gyms/g.py", '"n": 3', '"n": 4')], "sneaky change", "A")
    m = Maintainer(ledger, proj, tmp_path / "bk", test_cmd=[sys.executable, "-m", "pytest", "-q", "-x"])
    before = (proj / "src/antslab/gyms/g.py").read_text()
    r = m.attempt(drift, **KW)
    assert not r["applied"] and any("drifted" in x for x in r["reasons"])
    assert (proj / "src/antslab/gyms/g.py").read_text() == before


def test_patch_that_does_not_fix_the_failure_is_rejected(proj, ledger, tmp_path):
    noop = Patch([Edit("src/antslab/gyms/g.py", "def evaluate(spec):", 'def evaluate(spec):\n    """documented."""')])
    r = Maintainer(ledger, proj, tmp_path / "bk", test_cmd=[sys.executable, "-m", "pytest", "-q", "-x"]).attempt(noop, **KW)
    assert not r["applied"] and any("still fails" in x for x in r["reasons"])


def test_syntactically_broken_patch_is_stopped_by_the_sandbox_tests(proj, ledger, tmp_path):
    broken = Patch([Edit("src/antslab/gyms/g.py", '"n": 3', '"n": 3  # swallows the closing brace')])
    r = Maintainer(ledger, proj, tmp_path / "bk", test_cmd=[sys.executable, "-m", "pytest", "-q", "-x"]).attempt(broken, **KW)
    assert not r["applied"] and any("tests fail" in x or "does not parse" in x for x in r["reasons"])   # static gate or sandbox


def test_failing_tests_block_and_rate_limit_applies(proj, ledger, tmp_path):
    breaks = Patch([Edit("src/antslab/gyms/g.py", "10.0 / spec", "11.0 / spec")])
    m = Maintainer(ledger, proj, tmp_path / "bk", test_cmd=[sys.executable, "-m", "pytest", "-q", "-x"])
    r = m.attempt(breaks, **KW)
    assert not r["applied"]
    assert m.attempt(FIX, **KW)["applied"]
    other = Patch([Edit("src/antslab/gyms/g.py", "def evaluate(spec):", 'def evaluate(spec):\n    """again."""')], "second same day")
    r2 = m.attempt(other, **KW)
    assert not r2["applied"] and any("rate limit" in x for x in r2["reasons"])


def test_rollback_trigger_watches_post_patch_failure_rate(proj, ledger, tmp_path):
    import time
    m = Maintainer(ledger, proj, tmp_path / "bk")
    t0 = time.time()
    for i in range(12):
        ledger.enqueue("g", "a", i, {"x": i}, "t"); t = ledger.claim(60)
        ledger.finish(t["id"], 1, "failed" if i < 9 else "done", {"v": 1} if i >= 9 else None, "boom" if i < 9 else None)
    assert m.should_roll_back(t0 - 1) and not m.should_roll_back(time.time() + 1)


def test_draft_repair_treats_llm_output_as_untrusted():
    class C:
        def chat_json(self, msgs, temperature=0.2):
            return {"edits": [{"path": "src/antslab/ledger.py", "old": "a", "new": "b"}], "rationale": "r"}, {"ep": "B"}
    p = draft_repair(C(), "boom", {"f": "x"})
    assert p and p.edits[0].path.endswith("ledger.py")                       # drafted, but it will not pass check_patch
    class Junk:
        def chat_json(self, *a, **k): return {"edits": "nope"}, {}
    assert draft_repair(Junk(), "boom", {}) is None

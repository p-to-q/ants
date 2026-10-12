import time
from pathlib import Path
from antslab.archive import LocalDirArchive, archive_pending
from antslab.janitor import disk_level, execute_cleanup, plan_cleanup

TH = {"warn": 15, "clean": 12, "pause": 8, "hardstop": 6}


def test_disk_levels():
    got = [disk_level(x, TH) for x in (20, 14.9, 11.9, 7.9, 5.9)]
    assert got == ["ok", "warn", "clean", "pause", "hardstop"]


def _art(ledger, tmp_path, name, trial_id, kind="artifact", protected=False, archive=True, text=None):
    p = tmp_path / "files" / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(text or (name * 50))
    ledger.add_artifact(trial_id, p, kind, protected)
    return p


def test_cleanup_deletes_only_fully_verified_cold_files(ledger, tmp_path):
    ids = {n: ledger.enqueue("g", "a", i, {"i": i}, "t") for i, n in enumerate(
        ["ok", "running", "keep", "unarch", "mismatch", "prot", "best", "hot", "edited"])}
    ledger.claim(60, arm="a")          # claims some trial; make 'running' explicit below
    ledger.conn.execute("UPDATE trials SET state='done' WHERE id!=?", (ids["running"],))
    ledger.conn.execute("UPDATE trials SET state='running' WHERE id=?", (ids["running"],))
    paths = {
        "ok": _art(ledger, tmp_path, "ok", ids["ok"]),
        "running": _art(ledger, tmp_path, "running", ids["running"]),
        "keep": _art(ledger, tmp_path, "keep", ids["keep"]),
        "unarch": _art(ledger, tmp_path, "unarch", ids["unarch"]),
        "mismatch": _art(ledger, tmp_path, "mismatch", ids["mismatch"]),
        "prot": _art(ledger, tmp_path, "prot", ids["prot"], protected=True),
        "best": _art(ledger, tmp_path, "best", ids["best"], kind="model_best"),
        "hot": _art(ledger, tmp_path, "hot", ids["hot"]),
        "edited": _art(ledger, tmp_path, "edited", ids["edited"]),
    }
    arch = LocalDirArchive(tmp_path / "cos")
    skip_upload = {str(paths["unarch"])}
    for n, p in paths.items():
        if str(p) in skip_upload:
            continue
        a = ledger.artifact(str(p))
        key = f"k/{n}"
        uri = arch.put(p, key)
        ledger.mark_archived(str(p), uri, "0" * 64 if n == "mismatch" else arch.sha256_of(key))
    paths["edited"].write_text("changed after verification")
    old = time.time() - 10 * 3600
    ledger.conn.execute("UPDATE artifacts SET verified_at=? WHERE path NOT LIKE '%/hot'", (old,))
    plan = plan_cleanup(ledger, need_bytes=10**9, hot_s=6 * 3600, keep_trials=[ids["keep"]])
    assert [Path(a["path"]).name for a in plan] == ["ok", "edited"]       # 'edited' passes the plan...
    dry = execute_cleanup(ledger, plan, dry_run=True)
    assert dry["deleted"] == 1 and dry["skipped"] == 1 and paths["ok"].exists()   # dry-run re-verifies too
    res = execute_cleanup(ledger, plan)
    assert res["deleted"] == 1 and res["skipped"] == 1                    # ...but is re-verified and skipped
    assert not paths["ok"].exists()
    for n in ("running", "keep", "unarch", "mismatch", "prot", "best", "hot", "edited"):
        assert paths[n].exists(), n
    assert ledger.verify_chain()[0]


def test_archive_pending_verifies_by_readback(ledger, tmp_path):
    tid = ledger.enqueue("g", "a", 1, {}, "t")
    p = _art(ledger, tmp_path, "x", tid)
    good = LocalDirArchive(tmp_path / "cos")
    assert archive_pending(ledger, good)["uploaded"] == 1
    assert ledger.artifact(str(p))["verified_at"] is not None

    p2 = _art(ledger, tmp_path, "y", tid)

    class Corrupt(LocalDirArchive):
        def sha256_of(self, key):
            return "f" * 64
    assert archive_pending(ledger, Corrupt(tmp_path / "cos2"))["mismatch"] == 1
    assert ledger.artifact(str(p2))["verified_at"] is None


def test_file_changed_after_registration_is_not_archived(ledger, tmp_path):
    tid = ledger.enqueue("g", "a", 1, {}, "t")
    p = _art(ledger, tmp_path, "z", tid)
    p.write_text("different")
    s = archive_pending(ledger, LocalDirArchive(tmp_path / "cos"))
    assert s["changed"] == 1 and s["uploaded"] == 0

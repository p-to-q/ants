"""Archive / janitor failure paths: crashes mid-upload, vanished files, starvation, and 'verified before delete' at delete time."""
import time
from pathlib import Path

import pytest

from antslab.archive import LocalDirArchive, archive_pending
from antslab.janitor import execute_cleanup, plan_cleanup

HOT = 6 * 3600


def art(ledger, tmp_path, name, tid, kind="log", protected=False, text=None, size=None):
    p = tmp_path / "files" / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(text or (name * 20))
    if size:
        p.write_bytes(b"z" * size)
    ledger.add_artifact(tid, p, kind, protected)
    return p


def trial(ledger, state="done", arm="a"):
    tid = ledger.enqueue("g", arm, ledger.conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0], {"i": 1}, "t")
    if state != "queued":
        ledger.conn.execute("UPDATE trials SET state=? WHERE id=?", (state, tid))
    return tid


def verified(ledger, arch, p, age_s=10 * 3600):
    key = f"k/{p.name}"
    uri = arch.put(p, key)
    assert ledger.mark_archived(str(p), uri, arch.sha256_of(key))
    ledger.conn.execute("UPDATE artifacts SET verified_at=? WHERE path=?", (time.time() - age_s, str(p)))


# ---------------------------------------------------------------- archive_pending
class CrashAfterPut(LocalDirArchive):
    """put() succeeds, then the process 'dies' before the read-back / mark_archived."""
    def __init__(self, root, crashes=1):
        super().__init__(root); self.crashes = crashes

    def put(self, local, key):
        uri = super().put(local, key)
        if self.crashes:
            self.crashes -= 1
            raise KeyboardInterrupt("simulated crash between upload and mark_archived")
        return uri


def test_crash_between_upload_and_mark_archived_is_idempotent(ledger, tmp_path):
    tid = trial(ledger)
    p = art(ledger, tmp_path, "a.log.gz", tid)
    arch = CrashAfterPut(tmp_path / "cos")
    with pytest.raises(KeyboardInterrupt):
        archive_pending(ledger, arch)
    assert ledger.artifact(str(p))["verified_at"] is None                 # not claimed verified
    assert (tmp_path / "cos").exists()
    s = archive_pending(ledger, arch)                                     # the retry
    assert s["uploaded"] == 1
    a = ledger.artifact(str(p))
    assert a["verified_at"] is not None and a["archived_sha256"] == a["sha256"]
    assert [e["kind"] for e in ledger.events() if e["kind"].startswith("artifact_archived")] == ["artifact_archived"]
    assert archive_pending(ledger, arch) == {"uploaded": 0, "mismatch": 0, "missing": 0, "changed": 0, "errors": 0}
    assert ledger.verify_chain()[0]


def test_vanished_files_do_not_starve_the_queue(ledger, tmp_path):
    tid = trial(ledger)
    gone = [art(ledger, tmp_path, f"gone{i}", tid) for i in range(5)]
    good = art(ledger, tmp_path, "good", tid)
    for g in gone:
        g.unlink()
    arch = LocalDirArchive(tmp_path / "cos")
    s = archive_pending(ledger, arch, limit=3)                            # a window smaller than the number of vanished rows
    s2 = archive_pending(ledger, arch, limit=3)
    assert ledger.artifact(str(good))["verified_at"] is not None, (s, s2)
    assert s["missing"] + s2["missing"] == 5
    assert archive_pending(ledger, arch, limit=3)["missing"] == 0         # recorded once, not re-found forever
    assert any(e["kind"] == "artifact_missing" for e in ledger.events())
    assert ledger.verify_chain()[0]


def test_one_failing_upload_does_not_block_the_others(ledger, tmp_path):
    tid = trial(ledger)
    bad = art(ledger, tmp_path, "bad", tid)
    good = art(ledger, tmp_path, "good", tid)

    class Flaky(LocalDirArchive):
        def put(self, local, key):
            if local.name == "bad":
                raise ConnectionError("S3 says no")
            return super().put(local, key)
    s = archive_pending(ledger, Flaky(tmp_path / "cos"))
    assert ledger.artifact(str(good))["verified_at"] is not None
    assert ledger.artifact(str(bad))["verified_at"] is None and s["errors"] == 1


def test_unreadable_artifact_does_not_crash_archiving(ledger, tmp_path):
    tid = trial(ledger)
    p = art(ledger, tmp_path, "dirnow", tid)
    good = art(ledger, tmp_path, "good", tid)
    p.unlink(); p.mkdir()                                                 # sha256_file raises IsADirectoryError
    s = archive_pending(ledger, LocalDirArchive(tmp_path / "cos"))
    assert ledger.artifact(str(good))["verified_at"] is not None and s["errors"] == 1


def test_remote_mismatch_is_retried_next_time(ledger, tmp_path):
    tid = trial(ledger)
    p = art(ledger, tmp_path, "m", tid)

    class Corrupt(LocalDirArchive):
        def sha256_of(self, key):
            return "f" * 64
    assert archive_pending(ledger, Corrupt(tmp_path / "bad"))["mismatch"] == 1
    assert ledger.artifact(str(p))["verified_at"] is None
    s = archive_pending(ledger, LocalDirArchive(tmp_path / "good"))      # transient corruption is gone: it must be re-uploaded
    assert s["uploaded"] == 1 and ledger.artifact(str(p))["verified_at"] is not None


# ---------------------------------------------------------------- janitor
def test_delete_conditions_are_rechecked_at_delete_time(ledger, tmp_path):
    arch = LocalDirArchive(tmp_path / "cos")
    t_ok, t_prot, t_busy, t_hot, t_best = (trial(ledger) for _ in range(5))
    names = {}
    for n, tid in (("ok", t_ok), ("prot", t_prot), ("busy", t_busy), ("hot", t_hot), ("keep", t_best)):
        names[n] = art(ledger, tmp_path, n, tid)
        verified(ledger, arch, names[n])
    plan = plan_cleanup(ledger, 10**9, hot_s=HOT)
    assert {Path(a["path"]).name for a in plan} == set(names)
    # ...time passes between planning and executing, and the world changes:
    ledger.set_protected(str(names["prot"]))                              # someone protects a file
    ledger.conn.execute("UPDATE trials SET state='running' WHERE id=?", (t_busy,))      # its trial is re-queued / running
    ledger.conn.execute("UPDATE artifacts SET verified_at=? WHERE path=?", (time.time(), str(names["hot"])))   # re-verified just now
    ledger.conn.execute("UPDATE artifacts SET kind='model_best' WHERE path=?", (str(names["keep"]),))
    res = execute_cleanup(ledger, plan)
    assert res["deleted"] == 1 and res["skipped"] == 4, res
    assert not names["ok"].exists()
    for n in ("prot", "busy", "hot", "keep"):
        assert names[n].exists(), n
    assert ledger.verify_chain()[0]


def test_artifact_rewritten_after_planning_is_not_deleted(ledger, tmp_path):
    """add_artifact on the same path resets verification; the stale plan must not delete the new, unarchived bytes."""
    arch = LocalDirArchive(tmp_path / "cos")
    tid = trial(ledger)
    p = art(ledger, tmp_path, "r", tid)
    verified(ledger, arch, p)
    plan = plan_cleanup(ledger, 10**9, hot_s=HOT)
    p.write_text("brand new content")
    ledger.add_artifact(tid, p, "log")
    res = execute_cleanup(ledger, plan)
    assert res["deleted"] == 0 and p.exists()


def test_hash_mismatch_at_delete_time_skips_and_keeps_the_file(ledger, tmp_path):
    arch = LocalDirArchive(tmp_path / "cos")
    tid = trial(ledger)
    p = art(ledger, tmp_path, "h", tid)
    verified(ledger, arch, p)
    plan = plan_cleanup(ledger, 10**9, hot_s=HOT)
    p.write_text("bit rot")
    res = execute_cleanup(ledger, plan)
    assert res["deleted"] == 0 and res["skipped"] == 1 and p.exists()


def test_missing_file_does_not_wedge_cleanup(ledger, tmp_path):
    """A file deleted by hand (or unlinked just before a crash, before mark_deleted) used to stay in every plan,
    counting towards need_bytes while freeing nothing, so real files behind it were never reached."""
    arch = LocalDirArchive(tmp_path / "cos")
    tid = trial(ledger)
    ghosts = [art(ledger, tmp_path, f"ghost{i}", tid, size=5000) for i in range(3)]
    real = art(ledger, tmp_path, "real", tid, size=5000)
    for i, g in enumerate(ghosts):
        verified(ledger, arch, g, age_s=(20 - i) * 3600)                  # older than `real`: they sit at the head of the plan
    verified(ledger, arch, real, age_s=8 * 3600)
    for g in ghosts:
        g.unlink()
    for _ in range(2):
        plan = plan_cleanup(ledger, 5000, hot_s=HOT)
        execute_cleanup(ledger, plan)
    assert not real.exists(), "real file never reached: phantom entries filled the plan"
    assert all(ledger.artifact(str(g))["deleted_at"] is not None for g in ghosts)    # reconciled, not re-planned forever
    assert plan_cleanup(ledger, 10**9, hot_s=HOT) == []
    assert ledger.verify_chain()[0]


def test_unreadable_file_at_delete_time_is_skipped_not_fatal(ledger, tmp_path):
    arch = LocalDirArchive(tmp_path / "cos")
    tid = trial(ledger)
    a = art(ledger, tmp_path, "a", tid)
    b = art(ledger, tmp_path, "b", tid)
    verified(ledger, arch, a, age_s=20 * 3600)
    verified(ledger, arch, b, age_s=10 * 3600)
    plan = plan_cleanup(ledger, 10**9, hot_s=HOT)
    a.unlink(); a.mkdir()                                                 # now a directory: hashing it raises
    res = execute_cleanup(ledger, plan)
    assert not b.exists() and a.exists() and res["deleted"] == 1

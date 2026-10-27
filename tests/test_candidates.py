import json
from pathlib import Path

import pytest

from antslab import candidates as C
from antslab.ledger import Ledger
from antslab.series import compute_epoch


@pytest.fixture
def source(tmp_path, make_contract):
    c = make_contract()
    contract = tmp_path / "task.toml"
    epoch = compute_epoch(contract)
    led = Ledger(tmp_path / "run/ledger.sqlite")
    led.register_epoch(epoch, {})
    led.event("daemon_started", {"arm":"random","epoch":epoch,"arm_def_hash":"test"})
    def add(value, size=50, stage="s2", a=.5, row_epoch=None):
        spec={"a":a,"b":2,"c":"u"}
        led.enqueue(c.gym,"random",1,spec,"random",stage=stage,epoch=row_epoch or epoch,spec_key=c.space.key(spec))
        t=led.claim(60,arm="random",epoch=row_epoch or epoch)
        metrics={"val_mse":value}
        if size is not None:
            metrics["n_params"]=size
        led.finish(t["id"],t["attempt"],"done",metrics=metrics,cpu_s=1)
    yield led, contract, epoch, add
    led.close()


def test_new_best_replaces_pointer_but_keeps_earlier_version(source,tmp_path):
    led,contract,_,add=source
    store=tmp_path/"saved"
    add(.8)
    a=C.sync(Path(led.path).parent,contract,"random",store)
    first=a["caps"]["100"]
    assert C.read_record(store,first)["candidate"]["weights_preserved"] is False
    assert C.sync(Path(led.path).parent,contract,"random",store)["saved"] == []
    add(.7,a=.4)
    b=C.sync(Path(led.path).parent,contract,"random",store)
    assert b["caps"]["100"] != first
    assert C.read_record(store,first)["candidate"]["value"] == .8
    assert C.read_record(store,b["caps"]["100"])["candidate"]["value"] == .7


def test_shallow_missing_cap_and_over_cap_cannot_replace_finalist(source,tmp_path):
    led,contract,_,add=source
    add(.8)
    add(.001,stage="s0",a=.2)
    add(.002,size=None,a=.3)
    add(.003,size=9999,a=.4)
    out=C.sync(Path(led.path).parent,contract,"random",tmp_path/"saved")
    record=C.read_record(tmp_path/"saved",out["caps"]["100"])
    assert record["candidate"]["value"] == .8
    assert record["candidate"]["release_ready"] is False


def test_preservation_does_not_write_source_ledger(source,tmp_path):
    led,contract,_,add=source
    add(.8)
    before=led.conn.total_changes,led.head(),led.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    C.sync(Path(led.path).parent,contract,"random",tmp_path/"saved")
    assert before == (led.conn.total_changes,led.head(),led.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])


def test_pointer_never_regresses_when_source_state_is_rewound(source,tmp_path):
    led,contract,_,add=source
    add(.8)
    store=tmp_path/"saved"
    first=C.sync(Path(led.path).parent,contract,"random",store)
    # Simulate a source projection no longer containing the recorded best.
    # Chain verification alone does not audit mutable tables (roadmap 29).
    led.conn.execute("UPDATE trials SET metrics=?",(json.dumps({"val_mse":.9,"n_params":50}),))
    second=C.sync(Path(led.path).parent,contract,"random",store)
    assert second["caps"] == first["caps"] and second["saved"] == []
    assert second["retained_incumbent_caps"] == ["100","300"]


def test_epoch_mismatch_refuses_new_records(source,tmp_path):
    led,contract,_,add=source
    led.register_epoch("foreign",{})
    add(.5,row_epoch="foreign")
    with pytest.raises(ValueError,match="epoch"):
        C.sync(Path(led.path).parent,contract,"random",tmp_path/"saved")
    assert not (tmp_path/"saved").exists()


def test_corrupt_chain_refuses_preservation(source,tmp_path):
    led,contract,_,add=source
    add(.8)
    led.conn.execute("UPDATE events SET hash='broken' WHERE seq=1")
    with pytest.raises(ValueError,match="chain"):
        C.sync(Path(led.path).parent,contract,"random",tmp_path/"saved")


def test_receipt_corruption_is_detected(source,tmp_path):
    led,contract,_,add=source
    add(.8)
    store=tmp_path/"saved"
    out=C.sync(Path(led.path).parent,contract,"random",store)
    rid=out["caps"]["100"]
    path=store/"versions"/(rid+".json")
    record=json.loads(path.read_text())
    record["receipt"]["chain_head"]="fake"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError,match="receipt checksum"):
        C.read_record(store,rid)


def test_index_cannot_point_a_cap_at_another_caps_record(source,tmp_path):
    led,contract,_,add=source
    add(.8)
    store=tmp_path/"saved"
    out=C.sync(Path(led.path).parent,contract,"random",store)
    path=store/"index.json"
    index=json.loads(path.read_text())
    index["scopes"][out["scope"]]["caps"]["100"]=out["caps"]["300"]
    path.write_text(json.dumps(index))
    with pytest.raises(ValueError,match="pointer"):
        C.read_index(store)


def test_symlink_store_is_refused(source,tmp_path):
    led,contract,_,add=source
    add(.8)
    target=tmp_path/"target";target.mkdir()
    link=tmp_path/"link";link.symlink_to(target,target_is_directory=True)
    with pytest.raises(ValueError,match="symlink"):
        C.sync(Path(led.path).parent,contract,"random",link)


def test_missing_source_never_creates_ledger(tmp_path,make_contract):
    make_contract()
    with pytest.raises(FileNotFoundError):
        C.sync(tmp_path/"missing",tmp_path/"task.toml","random",tmp_path/"saved")
    assert not (tmp_path/"missing").exists()


def test_explicit_cli_sync_and_list(source,tmp_path,capsys):
    from antslab import cli
    led,contract,_,add=source
    add(.8)
    store=tmp_path/"saved"
    assert cli.main(["candidates","sync","--contract",str(contract),"--workdir",str(Path(led.path).parent),"--arm","random","--store",str(store)]) == 0
    assert json.loads(capsys.readouterr().out)["caps"]
    assert cli.main(["candidates","list","--store",str(store)]) == 0
    assert len(json.loads(capsys.readouterr().out)["scopes"]) == 1

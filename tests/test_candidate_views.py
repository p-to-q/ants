import pytest

from antslab.history import History
from antslab import ui


def test_best_and_latest_are_distinct_and_still_cap_filtered(ledger,make_contract):
    c=make_contract()
    for value,stage,size,seed in [(.5,"s2",50,1),(.8,"s2",50,2),(.01,"s0",50,3),(.001,"s2",9999,4)]:
        spec={"a":seed/10,"b":2,"c":"u"}
        ledger.enqueue(c.gym,"random",seed,spec,"random",stage=stage,epoch="current",spec_key=c.space.key(spec))
        t=ledger.claim(60,arm="random",epoch="current")
        ledger.finish(t["id"],t["attempt"],"done",metrics={"val_mse":value,"n_params":size})
    h=History.for_arm(ledger,c,"random")
    best=ui.leaderboard(h,c,order="best")["100"]
    latest=ui.leaderboard(h,c,order="latest")["100"]
    assert best[0]["value"] == .5 and best[0]["stage"] == "s2"
    assert latest[0]["value"] == .01 and latest[0]["stage"] == "s0"
    assert all(r["n_params"]<=100 and r["epoch"]=="current" and r["finished_at"] for r in latest)
    with pytest.raises(ValueError,match="order"):
        ui.leaderboard(h,c,order="random")


def test_leaderboard_order_key_changes_only_the_view():
    from antslab.tui import app,views
    import curses
    a=app.App.__new__(app.App)
    a.screen="board";a.ctx=views.Ctx();a.filt={};a.ranks={1:1}
    import threading
    a.refresh=threading.Event()
    # use the same event handler as the curses loop
    a.view=lambda: views.View([])
    a.on_key(ord("o"),curses)
    assert a.filt["board_order"]=="latest" and a.ranks=={}

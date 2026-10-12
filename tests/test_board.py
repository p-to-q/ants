import json
from antslab import bench
from antslab.board import build
from antslab.portfolio import build_portfolio


def test_board_renders_from_a_real_run_without_external_requests(tmp_path):
    c = bench.make_contract()
    r = bench.drive(lambda cc, s: build_portfolio(cc, s, with_tpe=False), c, 300, 1, checkpoints=(300,))
    r["hist"].update()
    d = build(r["hist"].l, c, ["arm"], tmp_path)
    page = (tmp_path / "board.html").read_text()
    assert "http://" not in page.replace("http-equiv", "") and "https://" not in page and "<script" not in page
    assert json.loads((tmp_path / "board.json").read_text())["arms"]["arm"]["evaluations"] >= 300
    assert d["chain_ok"] and "research paths" in page and "discovery rate" in page


def test_board_escapes_untrusted_text(tmp_path):
    from antslab.board import render
    d = {"task": "<script>alert(1)</script>", "primary": "p", "direction": "min", "generated": "now", "chain_ok": True,
         "chain_head": "x", "arms": {}, "judges": [{"judge": "<b>j</b>", "kind": "k", "predictions": 1, "resolved": 0}]}
    out = render(d)
    assert "<script>" not in out and "&lt;script&gt;" in out and "<b>j</b>" not in out

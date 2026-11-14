from __future__ import annotations

import json

from conftest import NOW, _id_of
from hwfont_schema import GlyphStore, Quality

from review.decisions import LOG_NAME, DecisionStatus
from review.inputs import load_inputs
from review.run import main, open_session


def _common(tmp_path, synth) -> list[str]:
    return [
        "--store", str(tmp_path / "store"), "--sidecar", str(synth.sidecar_path),
        "--candidates", str(synth.candidate_dir), "--raster", f"p0={synth.raster_path}",
    ]


def _init(tmp_path, synth) -> int:
    return main(["init", "--store", str(tmp_path / "store"), "--sidecar", str(synth.sidecar_path)])


def test_init_creates_store_and_refuses_rerun(tmp_path, synth, capsys):
    assert _init(tmp_path, synth) == 0
    assert (tmp_path / "store" / "store.db").exists()
    s = GlyphStore.open(tmp_path / "store")
    try:
        n = len(s.targets())
    finally:
        s.close()
    assert n > 0
    assert f"{n} targets" in capsys.readouterr().out
    assert _init(tmp_path, synth) == 1
    out = capsys.readouterr()
    assert "error:" in out.out + out.err and "store.db" in out.out + out.err
    # --force keeps the existing store and does not reseed
    assert main(["init", "--store", str(tmp_path / "store"), "--sidecar", str(synth.sidecar_path), "--force"]) == 0
    assert "0 targets" in capsys.readouterr().out


def test_init_with_targets_file(tmp_path, synth):
    tf = tmp_path / "t.json"
    tf.write_text(json.dumps([{"label": "a", "kind": "single", "required_count": 3}]))
    assert main(["init", "--store", str(tmp_path / "store"), "--sidecar", str(synth.sidecar_path),
                 "--targets", str(tf)]) == 0
    s = GlyphStore.open(tmp_path / "store")
    try:
        assert [(t.label, t.required_count) for t in s.targets()] == [("a", 3)]
    finally:
        s.close()


def test_apply_in_order_then_coverage_and_replay(tmp_path, synth, capsys):
    assert _init(tmp_path, synth) == 0
    inputs = load_inputs(synth.sidecar_path, [synth.candidate_dir], {"p0": synth.raster_path})
    h = _id_of(inputs, "h", region=0)
    x = _id_of(inputs, "c", region=0)
    y = _id_of(inputs, "s", region=0)
    capsys.readouterr()
    rc = main(["apply", *_common(tmp_path, synth), "--now", NOW,
               "--accept", h, "--quality", "marginal", "--relabel", f"{x}=Q", "--reject", y])
    out = capsys.readouterr().out
    assert rc == 0, out
    lines = [l for l in out.splitlines() if not l.startswith("  ")]
    assert lines == [f"accepted {h} 'h' (marginal)", f"relabelled {x} -> 'Q'", f"rejected {y}"]
    s = GlyphStore.open(tmp_path / "store")
    try:
        samples = s.all_samples()
        assert len(samples) == 1 and samples[0].quality == Quality.marginal
    finally:
        s.close()
    log = json.loads((synth.candidate_dir / LOG_NAME).read_text())
    assert log["decisions"][x]["label"] == "Q"
    assert log["decisions"][y]["status"] == DecisionStatus.rejected.value
    assert log["decisions"][h]["updated_at"] == NOW

    assert main(["coverage", *_common(tmp_path, synth)]) == 0
    assert "targets met" in capsys.readouterr().out

    assert main(["replay", *_common(tmp_path, synth), "--now", NOW]) == 0
    assert "replayed" in capsys.readouterr().out
    s = GlyphStore.open(tmp_path / "store")
    try:
        assert len(s.all_samples()) == 1
    finally:
        s.close()


def test_apply_box_and_reset(tmp_path, synth, capsys):
    assert _init(tmp_path, synth) == 0
    inputs = load_inputs(synth.sidecar_path, [synth.candidate_dir], {"p0": synth.raster_path})
    h = _id_of(inputs, "h", region=0)
    b = inputs.candidate(h).bbox
    box = f"{b.x:g},{b.y:g},{b.w + 1:g},{b.h:g}"
    assert main(["apply", *_common(tmp_path, synth), "--now", NOW, "--box", f"{h}={box}", "--reset", h]) == 0
    out = capsys.readouterr().out
    assert f"box {h} -> {box}" in out and f"reset {h}" in out


def test_apply_unknown_id_errors(tmp_path, synth, capsys):
    assert _init(tmp_path, synth) == 0
    capsys.readouterr()
    assert main(["apply", *_common(tmp_path, synth), "--now", NOW, "--accept", "nope"]) == 1
    out = capsys.readouterr()
    assert "error:" in out.out + out.err


def test_quality_without_accept_errors(tmp_path, synth, capsys):
    assert _init(tmp_path, synth) == 0
    assert main(["apply", *_common(tmp_path, synth), "--quality", "good"]) == 1
    out = capsys.readouterr()
    assert "error:" in out.out + out.err


def test_malformed_raster_errors(tmp_path, synth, capsys):
    assert _init(tmp_path, synth) == 0
    capsys.readouterr()
    rc = main(["coverage", "--store", str(tmp_path / "store"), "--sidecar", str(synth.sidecar_path),
               "--candidates", str(synth.candidate_dir), "--raster", "no-equals-sign"])
    assert rc == 1
    out = capsys.readouterr()
    assert "error:" in out.out + out.err


def test_open_session_routes_to_directory(tmp_path, synth):
    assert _init(tmp_path, synth) == 0
    sess = open_session(tmp_path / "store", synth.sidecar_path, [synth.candidate_dir],
                        {"p0": synth.raster_path}, None, NOW)
    try:
        h = next(iter(sess.inputs.pages["p0"].candidates))
        assert sess.dir_of(h) == synth.candidate_dir
        assert h in sess.log_for(h).decisions
    finally:
        sess.close()

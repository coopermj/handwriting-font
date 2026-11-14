from __future__ import annotations

import json

import pytest
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


# ---------- frozen session statistics (I4), manifest digest and consistency check (I5)


def _log_json(synth_dir) -> dict:
    return json.loads((synth_dir / LOG_NAME).read_text())


def _write_log_json(synth_dir, data: dict) -> None:
    (synth_dir / LOG_NAME).write_text(json.dumps(data))


def test_stats_frozen_at_first_open_and_restats(tmp_path, synth, capsys):
    from review.session import compute_stats

    assert _init(tmp_path, synth) == 0
    inputs = load_inputs(synth.sidecar_path, [synth.candidate_dir], {"p0": synth.raster_path})
    computed = compute_stats(inputs)
    sess = open_session(tmp_path / "store", synth.sidecar_path, [synth.candidate_dir], {"p0": synth.raster_path}, None, NOW)
    sess.close()
    data = _log_json(synth.candidate_dir)
    assert data["stats"] == computed.to_record(["p0"]).model_dump(mode="json")
    assert data["stats"]["page_ids"] == ["p0"]

    data["stats"]["x_height"] = 0.5  # frozen: later opens reuse the logged value, not a recomputation
    _write_log_json(synth.candidate_dir, data)
    sess = open_session(tmp_path / "store", synth.sidecar_path, [synth.candidate_dir], {"p0": synth.raster_path}, None, NOW)
    try:
        assert sess.stats.x_height == 0.5 and sess.stats.median_half_gap == computed.median_half_gap
    finally:
        sess.close()
    h = _id_of(inputs, "h", region=0)
    assert main(["apply", *_common(tmp_path, synth), "--now", NOW, "--accept", h]) == 0
    s = GlyphStore.open(tmp_path / "store")
    try:
        assert s.all_samples()[0].metrics.x_height == 0.5
    finally:
        s.close()

    capsys.readouterr()
    assert main(["replay", *_common(tmp_path, synth), "--now", NOW, "--restats"]) == 0
    assert "recomputed session statistics" in capsys.readouterr().out
    assert _log_json(synth.candidate_dir)["stats"]["x_height"] == computed.x_height
    s = GlyphStore.open(tmp_path / "store")
    try:
        assert s.all_samples()[0].metrics.x_height == computed.x_height
    finally:
        s.close()


def _two_pages(tmp_path):
    from hwfont_schema import CaptureSidecar
    from synth_page import synth_page

    a = synth_page(tmp_path / "a", ["the cat sat"], page_id="p0")
    b = synth_page(tmp_path / "b", ["a fat hen"], page_id="p1")
    sidecar = tmp_path / "two.sidecar.json"
    sidecar.write_text(CaptureSidecar(pages=[a.page, b.page]).model_dump_json())
    return sidecar, a, b


def test_stats_disagreeing_across_logs_need_restats(tmp_path, capsys):
    from review.errors import ReviewError

    sidecar, a, b = _two_pages(tmp_path)
    assert main(["init", "--store", str(tmp_path / "store"), "--sidecar", str(sidecar)]) == 0
    dirs = [a.candidate_dir, b.candidate_dir]
    open_session(tmp_path / "store", sidecar, dirs, None, None, NOW).close()
    ra, rb = _log_json(a.candidate_dir)["stats"], _log_json(b.candidate_dir)["stats"]
    assert ra == rb and ra["page_ids"] == ["p0", "p1"]
    data = _log_json(b.candidate_dir)
    data["stats"]["median_half_gap"] = 0.07
    _write_log_json(b.candidate_dir, data)
    with pytest.raises(ReviewError, match=r"review replay --restats"):
        open_session(tmp_path / "store", sidecar, dirs, None, None, NOW)
    common = ["--store", str(tmp_path / "store"), "--sidecar", str(sidecar),
              "--candidates", str(a.candidate_dir), "--candidates", str(b.candidate_dir)]
    assert main(["coverage", *common]) == 1
    assert "review replay --restats" in capsys.readouterr().err
    assert main(["replay", *common, "--restats"]) == 0
    assert _log_json(a.candidate_dir)["stats"] == _log_json(b.candidate_dir)["stats"] == ra


def test_changed_manifest_is_refused(tmp_path, synth):
    from review.errors import ReviewError

    assert _init(tmp_path, synth) == 0
    open_session(tmp_path / "store", synth.sidecar_path, [synth.candidate_dir], None, None, NOW).close()
    assert len(_log_json(synth.candidate_dir)["manifest_sha256"]) == 64
    manifest = synth.candidate_dir / "candidates.json"
    manifest.write_text(manifest.read_text() + "\n")
    with pytest.raises(ReviewError, match="candidates.json changed since review.json was written"):
        open_session(tmp_path / "store", synth.sidecar_path, [synth.candidate_dir], None, None, NOW)


def test_inconsistencies_reported_and_cleared_by_replay(tmp_path, synth, capsys):
    from review.normalize import sample_id

    assert _init(tmp_path, synth) == 0
    inputs = load_inputs(synth.sidecar_path, [synth.candidate_dir], {"p0": synth.raster_path})
    h, c = _id_of(inputs, "h", region=0), _id_of(inputs, "c", region=0)
    assert main(["apply", *_common(tmp_path, synth), "--now", NOW, "--accept", h, "--accept", c]) == 0
    s = GlyphStore.open(tmp_path / "store")
    try:
        s.remove_sample(sample_id("capture", h))  # store edited behind review's back
    finally:
        s.close()
    data = _log_json(synth.candidate_dir)
    data["decisions"][c]["status"] = "rejected"  # log edited behind the store's back
    _write_log_json(synth.candidate_dir, data)
    sess = open_session(tmp_path / "store", synth.sidecar_path, [synth.candidate_dir], {"p0": synth.raster_path}, None, NOW)
    try:
        assert sess.inconsistencies == sorted([
            f"{h} is accepted but sample capture-{h} is missing from the store",
            f"{c} is rejected but sample capture-{c} is in the store",
        ])
    finally:
        sess.close()
    capsys.readouterr()
    assert main(["coverage", *_common(tmp_path, synth)]) == 0
    err = capsys.readouterr().err
    assert f"warning: {h} is accepted" in err and "run `review replay`" in err
    assert main(["replay", *_common(tmp_path, synth), "--now", NOW]) == 0
    capsys.readouterr()
    assert main(["coverage", *_common(tmp_path, synth)]) == 0
    assert "warning" not in capsys.readouterr().err


def test_module_entry_point():  # I8
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "review.run", "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "replay" in r.stdout


def test_apply_prevalidates_every_id_before_writing(tmp_path, synth, capsys):  # minor
    assert _init(tmp_path, synth) == 0
    inputs = load_inputs(synth.sidecar_path, [synth.candidate_dir], {"p0": synth.raster_path})
    h = _id_of(inputs, "h", region=0)
    open_session(tmp_path / "store", synth.sidecar_path, [synth.candidate_dir], None, None, NOW).close()
    saved = (synth.candidate_dir / LOG_NAME).read_bytes()
    capsys.readouterr()
    assert main(["apply", *_common(tmp_path, synth), "--now", NOW, "--accept", h, "--accept", "nope"]) == 1
    out = capsys.readouterr()
    assert "nope" in out.err and "accepted" not in out.out
    s = GlyphStore.open(tmp_path / "store")
    try:
        assert s.all_samples() == []
    finally:
        s.close()
    assert (synth.candidate_dir / LOG_NAME).read_bytes() == saved


def test_init_force_with_targets_notices_existing_targets(tmp_path, synth, capsys):  # minor
    assert _init(tmp_path, synth) == 0
    tf = tmp_path / "t.json"
    tf.write_text(json.dumps([{"label": "a", "kind": "single", "required_count": 3}]))
    capsys.readouterr()
    assert main(["init", "--store", str(tmp_path / "store"), "--sidecar", str(synth.sidecar_path),
                 "--force", "--targets", str(tf)]) == 0
    out = capsys.readouterr().out
    assert "notice:" in out and "ignored" in out and "0 targets" in out

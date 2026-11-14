from __future__ import annotations

import dataclasses
import io

import pytest
from conftest import NOW, _id_of
from font_gen.load import store_digest
from hwfont_schema import BBox, CaptureSidecar, GlyphStore, Kind, Target
from hwfont_schema.emspace import check_sample_em
from PIL import Image

from review.decisions import LOG_NAME, DecisionStatus, accept, adjust_box, load_log, reject, relabel
from review.errors import ReviewError
from review.normalize import sample_id
from review.store_sync import (
    LIGATURE_REQUIRED,
    SINGLE_REQUIRED,
    apply,
    mirror,
    replay,
    seed_targets,
    targets_from_sidecar,
)


def test_targets_from_sidecar(synth):
    sidecar = CaptureSidecar.model_validate_json(synth.sidecar_path.read_text())
    targets = targets_from_sidecar(sidecar)
    ts = {t.label: t for t in targets}
    assert ts["a"] == Target(label="a", kind=Kind.single, required_count=SINGLE_REQUIRED)
    assert ts["th"] == Target(label="th", kind=Kind.ligature, required_count=LIGATURE_REQUIRED)
    chars = set("the cat sat" + "a fat hen ate" + "the hat") - {" "}
    assert set(ts) == chars | {"th"} == set("thecasfnt") | {"th"}
    assert [t.label for t in targets] == sorted(ts) and len(targets) == len(ts)
    assert (SINGLE_REQUIRED, LIGATURE_REQUIRED) == (12, 8)


def test_seed_targets_once(store, inputs, tmp_path):
    n = seed_targets(store, inputs)
    assert n == len(targets_from_sidecar(inputs.sidecar)) > 0
    assert seed_targets(store, inputs) == 0
    assert store.targets() == targets_from_sidecar(inputs.sidecar)
    other = GlyphStore.create(tmp_path / "s2")
    try:
        assert seed_targets(other, inputs, [Target(label="z", kind=Kind.single, required_count=1)]) == 1
        assert [t.label for t in other.targets()] == ["z"]
    finally:
        other.close()


def test_accept_writes_and_reject_removes(store, inputs, stats, log, synth):
    cid = _id_of(inputs, "h", region=0)
    log2, d = apply(store, inputs, stats, log, synth.candidate_dir, lambda l: accept(l, cid, NOW), cid)
    sid = sample_id("capture", cid); s = store.get_sample(sid)
    assert s is not None and s.strokes_path == f"strokes/{sid}.json" and s.raster_path == f"raster/{sid}.png"
    assert check_sample_em(s, store.load_strokes(s), store.load_raster(s)) == []
    assert d.status == DecisionStatus.accepted and d.quality == s.quality and log2.decisions[cid] == d
    assert load_log(synth.candidate_dir, inputs, NOW).decisions[cid].status == DecisionStatus.accepted
    log3, d3 = apply(store, inputs, stats, log2, synth.candidate_dir, lambda l: reject(l, cid, NOW), cid)
    assert store.get_sample(sid) is None and not (store.root / "strokes" / f"{sid}.json").exists()
    assert not (store.root / "raster" / f"{sid}.png").exists()
    assert d3.status == DecisionStatus.rejected
    assert load_log(synth.candidate_dir, inputs, NOW).decisions[cid].status == DecisionStatus.rejected


def test_mirror_unknown_candidate_is_an_error(store, inputs, stats, log):
    with pytest.raises(ReviewError):
        mirror(store, inputs, stats, log, "nope")


def test_relabel_accepted_rewrites_with_new_kind(store, inputs, stats, log, synth):   # Review Focus 4
    cid = _id_of(inputs, "a", region=1); sid = sample_id("capture", cid)
    log2, _ = apply(store, inputs, stats, log, synth.candidate_dir, lambda l: accept(l, cid, NOW), cid)
    log3, d = apply(store, inputs, stats, log2, synth.candidate_dir, lambda l: relabel(l, inputs, cid, "th", NOW), cid)
    assert [s.id for s in store.all_samples()] == [sid] and store.get_sample(sid).kind == Kind.ligature
    assert store.get_sample(sid).label == "th"
    assert sorted(p.name for p in (store.root / "strokes").iterdir()) == [f"{sid}.json"]


def test_materialise_error_leaves_store_and_log(store, inputs, stats, log, synth):
    cid = next(iter(log.decisions)); pg = inputs.pages["p0"]
    blank = Image.new("L", (4, 4), 255); buf = io.BytesIO(); blank.save(buf, "PNG")
    pg2 = dataclasses.replace(pg, strokes={k: v for k, v in pg.strokes.items() if k != cid}, crops={**pg.crops, cid: buf.getvalue()}, raster=None)
    bad = dataclasses.replace(inputs, pages={"p0": pg2})
    path = synth.candidate_dir / LOG_NAME
    before = path.read_bytes() if path.exists() else None
    snapshot = log.model_copy(deep=True)
    with pytest.raises(ReviewError, match="no ink"):
        apply(store, bad, stats, log, synth.candidate_dir, lambda l: accept(l, cid, NOW), cid)
    assert store.all_samples() == []
    assert (path.read_bytes() if path.exists() else None) == before
    assert log == snapshot


def test_replay_equals_write_through(store, inputs, stats, log, synth, tmp_path):
    ids = list(log.decisions)[:4]; cur = log
    for cid in ids:
        cur, _ = apply(store, inputs, stats, cur, synth.candidate_dir, lambda l, c=cid: accept(l, c, NOW), cid)
    other = GlyphStore.create(tmp_path / "other")
    try:
        seed_targets(other, inputs); seed_targets(store, inputs)
        replayed = replay(other, inputs, stats, load_log(synth.candidate_dir, inputs, NOW))
        assert store_digest(other) == store_digest(store)
        assert len(other.all_samples()) == 4
        assert replayed == cur
    finally:
        other.close()


def test_neighbour_adjustment_leaves_accepted_sample_alone(store, inputs, stats, log, synth):
    t, h = (_id_of(inputs, c, region=0) for c in "th")
    log2, _ = apply(store, inputs, stats, log, synth.candidate_dir, lambda l: accept(l, t, NOW), t)
    before = store.get_sample(sample_id("capture", t))
    small = BBox(x=synth.page.regions[0].bbox.x + 1, y=synth.page.regions[0].bbox.y + 1, w=5, h=5)
    apply(store, inputs, stats, log2, synth.candidate_dir, lambda l: adjust_box(l, inputs, h, small, NOW), h)
    assert store.get_sample(sample_id("capture", t)) == before

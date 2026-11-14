from __future__ import annotations

import dataclasses
import io

import pytest
from conftest import NOW, _id_of
from hwfont_schema import BBox, Contour, Kind, Quality, ReviewStatus, StrokeData, StrokePoint
from hwfont_schema.emspace import check_sample_em, png_size
from PIL import Image
from synth_page import synth_page

from review.decisions import adjust_box, default_log, relabel
from review.errors import ReviewError
from review.ink import candidate_extent
from review.inputs import load_inputs
from review.normalize import ADVANCE_CLAMP, STUB_EXTENT, materialise
from review.session import compute_stats, neighbours, unit_order


def test_frame_scale_and_flip(inputs, stats, log, synth):
    cid = _id_of(inputs, "h", region=0); m = materialise(inputs, stats, log, cid)
    region = synth.page.regions[0]; pts = [p for c in m.strokes.contours for p in c.points]
    assert m.sample.id == f"capture-{cid}" and m.sample.metrics.baseline == 0.0 and m.sample.created_at == NOW
    assert max(p.y for p in pts) == pytest.approx(0.70, abs=0.02)     # ascender in em, y-up
    assert m.sample.metrics.x_height == stats.x_height and m.sample.review_status == ReviewStatus.accepted
    assert m.sample.context == inputs.candidate(cid).context and m.sample.capture_session_id == "capture"
    assert check_sample_em(m.sample, m.strokes, m.raster) == [] and m.flags == ()
    assert m.sample.kind == Kind.single and m.sample.quality == Quality.good

def test_half_gap_sidebearings(inputs, stats, log):
    a = _id_of(inputs, "a", region=1)                                  # 'a' of "a fat": isolated word
    m = materialise(inputs, stats, log, a)
    assert m.sample.metrics.bbox.x == pytest.approx(stats.median_half_gap)   # no neighbours → session median both sides
    assert m.sample.metrics.advance == pytest.approx(2 * stats.median_half_gap + m.sample.metrics.bbox.w)
    h = _id_of(inputs, "h", region=0)                                   # 'h' of "the"
    pg = inputs.pages["p0"]; left, right = neighbours(pg, pg.candidates[h].region_id, h)
    eh, el, er = (candidate_extent(pg.candidates[i], pg.strokes[i], None) for i in (h, left, right))
    mh = materialise(inputs, stats, log, h)
    assert mh.sample.metrics.bbox.x == pytest.approx(0.5 * (eh.xmin - el.xmax) / 70)
    assert mh.sample.metrics.advance == pytest.approx((0.5 * (eh.xmin - el.xmax) + (eh.xmax - eh.xmin) + 0.5 * (er.xmin - eh.xmax)) / 70)

def _shift(strokes: StrokeData, dx: float) -> StrokeData:
    return StrokeData(contours=[Contour(points=[StrokePoint(x=p.x + dx, y=p.y) for p in c.points]) for c in strokes.contours])

def test_negative_half_gap_kept_and_clamp_marks_marginal(tmp_path):
    sp = synth_page(tmp_path, ["ll"]); base = load_inputs(sp.sidecar_path, [sp.candidate_dir]); pg = base.pages["p0"]
    a, b = unit_order(pg, sp.page.regions[0].id)
    ea = candidate_extent(pg.candidates[a], pg.strokes[a], None)
    eb = candidate_extent(pg.candidates[b], pg.strokes[b], None); wa = ea.xmax - ea.xmin   # an 'l' is a 0.02 em stem
    # 1. slide b's ink left until it overlaps half of a's width: both half-gaps go negative and are kept
    dx = -(eb.xmin - ea.xmax) - wa / 2
    over = dataclasses.replace(base, pages={"p0": dataclasses.replace(pg, strokes={**pg.strokes, b: _shift(pg.strokes[b], dx)})})
    stats = compute_stats(over); log = default_log(over, NOW)
    mb = materialise(over, stats, log, b); ma = materialise(over, stats, log, a)
    assert mb.sample.metrics.bbox.x < 0 and ma.sample.metrics.advance < ma.sample.metrics.bbox.x + ma.sample.metrics.bbox.w
    assert mb.sample.quality == Quality.good and check_sample_em(mb.sample, mb.strokes, None) == []
    # 2. b becomes a stub buried inside a's ink (ends before a's xmax): LSB = RSB ≈ −(a's width)/2, so advance ≤ 0 → clamp
    stub = StrokeData(contours=[Contour(points=[StrokePoint(x=ea.xmin, y=sp.page.regions[0].baseline_y - 10),
                                                 StrokePoint(x=ea.xmin + wa / 4, y=sp.page.regions[0].baseline_y - 11)])])
    deep = dataclasses.replace(base, pages={"p0": dataclasses.replace(pg, strokes={**pg.strokes, b: stub})})
    m = materialise(deep, compute_stats(deep), default_log(deep, NOW), b)
    assert m.sample.metrics.advance == ADVANCE_CLAMP and m.sample.quality == Quality.marginal and "advance_clamped" in m.flags

def test_punctuation_first_unit_uses_median_not_measured_gap(tmp_path):   # Review Focus 3
    sp = synth_page(tmp_path, [". the"]); inputs = load_inputs(sp.sidecar_path, [sp.candidate_dir])
    stats = compute_stats(inputs); log = default_log(inputs, NOW)
    dot = _id_of(inputs, ".", region=0); m = materialise(inputs, stats, log, dot)
    assert m.sample.metrics.advance == pytest.approx(2 * stats.median_half_gap + m.sample.metrics.bbox.w)

def test_neighbor_mismatch_falls_back_to_median_and_flags(inputs, stats, log, synth):
    pg = inputs.pages["p0"]; r0 = synth.page.regions[0].id; ids = unit_order(pg, r0)   # "the cat sat"
    bad = pg.candidates[ids[3]].model_copy(update={"label": "q"})
    inputs2 = dataclasses.replace(inputs, pages={"p0": dataclasses.replace(pg, candidates={**pg.candidates, ids[3]: bad})})
    m = materialise(inputs2, compute_stats(inputs2), default_log(inputs2, NOW), ids[4])     # 'a' of "cat"
    assert "neighbor_mismatch" in m.flags and m.sample.quality == Quality.good
    assert m.sample.metrics.bbox.x == pytest.approx(compute_stats(inputs2).median_half_gap)

def test_adjusting_a_box_does_not_change_the_neighbour(inputs, stats, log, synth):
    t, h = (_id_of(inputs, c, region=0) for c in "th")
    before = materialise(inputs, stats, log, t)
    log2 = adjust_box(log, inputs, h, BBox(x=synth.page.regions[0].bbox.x + 1, y=synth.page.regions[0].bbox.y + 1, w=5, h=5), NOW)
    assert materialise(inputs, stats, log2, t) == before

def test_adjusted_box_reassigns_strokes_from_pool(inputs, stats, log, synth):
    t, h = (_id_of(inputs, c, region=0) for c in "th")
    pg = inputs.pages["p0"]; bt, bh = pg.candidates[t].bbox, pg.candidates[h].bbox
    rb = synth.page.regions[0].bbox   # candidate boxes overhang the region by BOX_PAD; adjust_box needs the region
    wide = BBox(x=bt.x, y=rb.y, w=bh.x + bh.w - bt.x, h=rb.h)
    log2 = relabel(adjust_box(log, inputs, t, wide, NOW), inputs, t, "th", NOW)
    m = materialise(inputs, stats, log2, t)
    assert len(m.strokes.contours) == len(synth.ink[t]) + len(synth.ink[h]) and m.sample.kind == Kind.ligature
    assert check_sample_em(m.sample, m.strokes, m.raster) == []

def test_raster_paths(inputs, stats, log, synth):
    cid = _id_of(inputs, "e", region=0)
    with_page = materialise(inputs, stats, log, cid)
    assert with_page.raster is not None and check_sample_em(with_page.sample, with_page.strokes, with_page.raster) == []
    w, h = png_size(with_page.raster); assert w / (with_page.sample.metrics.bbox.w + 0.1) == pytest.approx(256, rel=0.02)
    no_page = load_inputs(synth.sidecar_path, [synth.candidate_dir])
    assert materialise(no_page, stats, default_log(no_page, NOW), cid).raster is None
    # raster-only: drop the stroke file from the inputs → crop path
    ro = dataclasses.replace(no_page.pages["p0"], strokes={k: v for k, v in no_page.pages["p0"].strokes.items() if k != cid})
    ro_inputs = dataclasses.replace(no_page, pages={"p0": ro})
    m = materialise(ro_inputs, stats, default_log(ro_inputs, NOW), cid)
    assert m.strokes is None and m.raster is not None and "raster_only" in m.flags
    assert check_sample_em(m.sample, None, m.raster) == []

def test_pressure_and_width_pass_through(inputs, stats, log):
    cid = _id_of(inputs, "c", region=0); pg = inputs.pages["p0"]
    src = pg.strokes[cid]; pts = [StrokePoint(x=p.x, y=p.y, pressure=0.5, width=3.5) for p in src.contours[0].points]
    pg2 = dataclasses.replace(pg, strokes={**pg.strokes, cid: StrokeData(contours=[Contour(points=pts)])})
    m = materialise(dataclasses.replace(inputs, pages={"p0": pg2}), stats, log, cid)
    assert all(p.pressure == 0.5 and p.width == pytest.approx(3.5 / 70) for p in m.strokes.contours[0].points)

def test_no_ink_is_an_error(inputs, stats, log):
    cid = next(iter(log.decisions)); pg = inputs.pages["p0"]
    blank = Image.new("L", (4, 4), 255); buf = io.BytesIO(); blank.save(buf, "PNG")
    pg2 = dataclasses.replace(pg, strokes={k: v for k, v in pg.strokes.items() if k != cid}, crops={**pg.crops, cid: buf.getvalue()}, raster=None)
    with pytest.raises(ReviewError, match="no ink"):
        materialise(dataclasses.replace(inputs, pages={"p0": pg2}), stats, log, cid)

def test_every_synthetic_candidate_conforms(inputs, stats, log):
    for cid in log.decisions:
        m = materialise(inputs, stats, log, cid)
        assert check_sample_em(m.sample, m.strokes, m.raster) == [], cid
        assert m.sample.quality == Quality.good and "advance_clamped" not in m.flags, cid

@pytest.mark.parametrize("axis", ["horizontal", "vertical"])
def test_zero_extent_stub_is_widened_and_flagged(inputs, stats, log, axis):
    cid = _id_of(inputs, "c", region=0); pg = inputs.pages["p0"]
    e = candidate_extent(pg.candidates[cid], pg.strokes[cid], None)
    x0, y0 = e.xmin + 2, e.ymax - 5
    x1, y1 = (x0 + 4, y0) if axis == "horizontal" else (x0, y0 - 4)
    stub = StrokeData(contours=[Contour(points=[StrokePoint(x=x0, y=y0), StrokePoint(x=x1, y=y1)])])
    m = materialise(dataclasses.replace(inputs, pages={"p0": dataclasses.replace(pg, strokes={**pg.strokes, cid: stub})}), stats, log, cid)
    b = m.sample.metrics.bbox
    assert (b.h if axis == "horizontal" else b.w) == STUB_EXTENT
    assert "stub_extent" in m.flags and m.sample.quality == Quality.good and m.raster is not None
    assert check_sample_em(m.sample, m.strokes, m.raster) == []

def test_box_widened_over_neighbour_uses_median_and_flags(inputs, stats, log, synth):  # I2
    t, h = (_id_of(inputs, c, region=0) for c in "th")
    pg = inputs.pages["p0"]; bt, bh = pg.candidates[t].bbox, pg.candidates[h].bbox
    rb = synth.page.regions[0].bbox
    wide = BBox(x=bt.x, y=rb.y, w=bh.x + bh.w - bt.x, h=rb.h)
    m = materialise(inputs, stats, relabel(adjust_box(log, inputs, t, wide, NOW), inputs, t, "th", NOW), t)
    b = m.sample.metrics.bbox
    assert "neighbor_swallowed" in m.flags and m.sample.quality == Quality.good
    assert m.sample.metrics.advance - (b.x + b.w) == pytest.approx(stats.median_half_gap)
    assert m.sample.metrics.advance > b.x + b.w
    assert "neighbor_swallowed" not in materialise(inputs, stats, log, t).flags

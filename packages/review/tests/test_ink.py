from __future__ import annotations

import io

from conftest import NOW
from hwfont_schema import (
    BBox,
    Candidate,
    CandidateStatus,
    Context,
    Contour,
    Kind,
    Metrics,
    PositionInWord,
    Quality,
    ReviewStatus,
    Sample,
    StrokeData,
    StrokePoint,
)
from hwfont_schema.emspace import check_sample_em, png_size
from PIL import Image

from review.ink import (
    Extent,
    assign_strokes,
    candidate_extent,
    composite_page,
    crop_extent,
    crop_origin,
    polylines_to_strokes,
    region_pool,
    resample_raster,
)
from review.inputs import load_inputs

_CTX = Context(source_word="o", position_in_word=PositionInWord.isolated)


def _candidate(bbox: BBox) -> Candidate:
    return Candidate(
        id="c",
        page_id="p0",
        region_id="p0-r0",
        label="a",
        kind=Kind.single,
        confidence=0.9,
        bbox=bbox,
        context=_CTX,
        status=CandidateStatus.pending,
        alignment_method="fiducial",
        model="synthetic",
        created_at=NOW,
    )


def _sample_with(bbox: BBox) -> Sample:
    return Sample(
        id="s",
        label="o",
        kind=Kind.single,
        context=_CTX,
        metrics=Metrics(baseline=0.0, x_height=0.45, advance=0.6, bbox=bbox),
        quality=Quality.good,
        review_status=ReviewStatus.accepted,
        capture_session_id="sess",
        created_at=NOW,
    )


def test_crop_origin_truncates():
    assert crop_origin(BBox(x=10.9, y=20.1, w=5, h=5)) == (10, 20)


def test_crop_extent_in_page_px():
    img = Image.new("L", (10, 8), 255)
    img.putpixel((3, 2), 0)
    img.putpixel((6, 5), 40)
    img.putpixel((7, 1), 200)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    assert crop_extent(buf.getvalue(), (100, 50)) == Extent(xmin=103, xmax=107, ymin=52, ymax=56)
    blank = Image.new("L", (4, 4), 255)
    b2 = io.BytesIO()
    blank.save(b2, "PNG")
    assert crop_extent(b2.getvalue(), (0, 0)) is None


def test_stroke_extent_uses_points_as_stored():  # Review Focus 1
    strokes = StrokeData(contours=[Contour(points=[StrokePoint(x=0, y=0), StrokePoint(x=10, y=4)])])
    cand = _candidate(bbox=BBox(x=100, y=100, w=5, h=5))  # box far from the points
    assert candidate_extent(cand, strokes, None) == Extent(0, 10, 0, 4)


def test_region_pool_dedupes_and_assignment_reproduces_ingest(inputs, synth):
    pg = inputs.pages["p0"]
    r0 = synth.page.regions[0].id
    pool = region_pool(pg, r0)
    assert len(pool) == sum(len(synth.ink[cid]) for cid in pg.candidate_ids(r0))
    for cid in pg.candidate_ids(r0):
        assert assign_strokes(pool, pg.candidates[cid].bbox) == synth.ink[cid]


def test_assign_strokes_fraction_rule():
    pool = [[(0, 0), (1, 1), (10, 10), (11, 11)], [(0, 0), (1, 1), (2, 2), (10, 10)]]
    assert assign_strokes(pool, BBox(x=0, y=0, w=3, h=3)) == pool  # 2/4 and 3/4 inside


def test_assign_strokes_skips_empty_and_below_fraction():
    pool = [[], [(0, 0), (10, 10), (11, 11)]]
    assert assign_strokes(pool, BBox(x=0, y=0, w=3, h=3)) == []


def test_polylines_to_strokes_keeps_template_pressure():
    template = StrokeData(
        contours=[Contour(points=[StrokePoint(x=0, y=0, pressure=0.3, width=0.01), StrokePoint(x=1, y=1, pressure=0.6)])]
    )
    out = polylines_to_strokes([[(0.0, 0.0), (1.0, 1.0)], [(5.0, 5.0), (6.0, 6.0)], [(9.0, 9.0)]], template)
    assert out is not None and len(out.contours) == 2
    assert [(p.pressure, p.width) for p in out.contours[0].points] == [(0.3, 0.01), (0.6, None)]
    assert all(p.pressure is None for p in out.contours[1].points)
    assert polylines_to_strokes([]) is None
    assert polylines_to_strokes([[(1.0, 1.0)]]) is None


def test_resample_raster_size_scale_and_white_padding():
    src = Image.new("L", (70, 70), 0)  # all ink, origin (0, 0)
    bbox = BBox(x=0.05, y=0.0, w=0.5, h=0.45)
    png = resample_raster(src, (0, 0), bbox, origin_x_px=-3.5, baseline_px=70, s=1 / 70)
    w, h = png_size(png)
    assert (w, h) == (round(0.6 * 256), round(0.55 * 256))
    assert check_sample_em(_sample_with(bbox), None, png) == []
    img = Image.open(io.BytesIO(png))
    # beyond the 70-px source -> white: left pad maps to source x < 0 (origin_x_px = -3.5),
    # bottom pad to source y > 70 (below baseline_px = 70).
    # (The brief's (w - 1, 0) maps to source (38.3, 35), inside the all-ink source.)
    assert img.getpixel((0, 0)) == 255
    assert img.getpixel((w - 1, h - 1)) == 255
    assert img.getpixel((w // 2, h // 2)) == 0


def test_composite_page_matches_raster_or_crops(inputs, synth):
    pg = inputs.pages["p0"]
    assert composite_page(pg).size == (pg.page.width_px, pg.page.height_px)
    no_raster = load_inputs(synth.sidecar_path, [synth.candidate_dir]).pages["p0"]
    comp = composite_page(no_raster)
    cid = next(iter(pg.candidates))
    ox, oy = crop_origin(pg.candidates[cid].bbox)
    x, y = synth.ink[cid][0][0]
    assert comp.getpixel((int(x), int(y))) < 128 and comp.getpixel((5, 5)) == 255

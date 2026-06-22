from PIL import Image

from hwfont_schema import BBox, CandidateStatus, Region
from ingest_segment.segment import VisionBox, VisionResult, segment_region


def _region():
    return Region(
        id="p0-r0",
        expected_transcript="cat",
        baseline_y=60.0,
        bbox=BBox(x=10.0, y=20.0, w=120.0, h=40.0),
        expected_units=["c", "a", "t"],
    )


def _raster():
    return Image.new("L", (200, 200), color=255)


def _vision_three_boxes(crop_png, region):
    # three single glyphs left-to-right within the crop (120x40)
    return VisionResult(
        boxes=[
            VisionBox(label="c", kind="single", x=0, y=0, w=30, h=40, confidence=0.9),
            VisionBox(label="a", kind="single", x=40, y=0, w=30, h=40, confidence=0.85),
            VisionBox(label="t", kind="single", x=80, y=0, w=30, h=40, confidence=0.8),
        ]
    )


def test_segment_region_builds_candidates_with_pagepx_bbox():
    region = _region()
    # one stroke clearly inside the first box (page-px ~ x in [10,40], y in [20,60])
    strokes = [[(15.0, 30.0), (20.0, 40.0), (25.0, 35.0)]]
    results = segment_region(
        region=region,
        raster=_raster(),
        page_strokes=strokes,
        vision=_vision_three_boxes,
        page_id="p0",
        alignment_method="fiducial",
        page_low_confidence=False,
        model="claude-opus-4-8",
        created_at="2026-06-22T00:00:00Z",
    )
    assert len(results) == 3
    first_cand, first_strokes = results[0]
    assert first_cand.label == "c"
    # crop box x=0 -> page-px x=region.bbox.x (10)
    assert abs(first_cand.bbox.x - 10.0) < 1e-6
    assert abs(first_cand.bbox.y - 20.0) < 1e-6
    assert first_cand.context.source_word == "cat"
    assert first_cand.status == CandidateStatus.pending
    assert len(first_strokes) == 1  # the inside stroke went to box 'c'
    assert len(results[1][1]) == 0  # no strokes for 'a'


def test_segment_region_flags_count_mismatch():
    region = _region()

    def vision_two(crop_png, r):
        return VisionResult(
            boxes=[
                VisionBox(label="c", kind="single", x=0, y=0, w=30, h=40, confidence=0.9),
                VisionBox(label="a", kind="single", x=40, y=0, w=30, h=40, confidence=0.9),
            ]
        )

    results = segment_region(
        region=region, raster=_raster(), page_strokes=[], vision=vision_two,
        page_id="p0", alignment_method="fiducial", page_low_confidence=False,
        model="claude-opus-4-8", created_at="2026-06-22T00:00:00Z",
    )
    # 2 boxes vs 3 expected units -> all flagged needs_review
    assert all(c.status == CandidateStatus.needs_review for c, _ in results)


def test_segment_region_flags_low_confidence_box_and_page():
    region = _region()

    def vision_lowconf(crop_png, r):
        return VisionResult(
            boxes=[
                VisionBox(label="c", kind="single", x=0, y=0, w=30, h=40, confidence=0.2),
                VisionBox(label="a", kind="single", x=40, y=0, w=30, h=40, confidence=0.9),
                VisionBox(label="t", kind="single", x=80, y=0, w=30, h=40, confidence=0.9),
            ]
        )

    results = segment_region(
        region=region, raster=_raster(), page_strokes=[], vision=vision_lowconf,
        page_id="p0", alignment_method="geometric_scale", page_low_confidence=True,
        model="claude-opus-4-8", created_at="2026-06-22T00:00:00Z",
    )
    by_label = {c.label: c for c, _ in results}
    assert by_label["c"].status == CandidateStatus.needs_review  # low confidence box
    assert by_label["a"].status == CandidateStatus.needs_review  # page low confidence

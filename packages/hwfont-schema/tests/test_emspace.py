import struct
import zlib

import pytest

from hwfont_schema import BBox, Context, Contour, Kind, Metrics, PositionInWord, Quality, ReviewStatus, Sample, StrokeData, StrokePoint
from hwfont_schema.emspace import (
    EM_CONVENTION_VERSION,
    RASTER_PAD,
    check_sample_em,
    png_size,
    raster_em_to_px,
    raster_px_to_em,
)


def _sample(baseline=0.0, x_height=0.45, advance=0.6, bbox=BBox(x=0.05, y=0.0, w=0.5, h=0.5)):
    return Sample(
        id="s1", label="a", kind=Kind.single,
        context=Context(source_word="cat", left_neighbor="c", right_neighbor="t", position_in_word=PositionInWord.medial),
        metrics=Metrics(baseline=baseline, x_height=x_height, advance=advance, bbox=bbox),
        quality=Quality.good, review_status=ReviewStatus.accepted,
        capture_session_id="sess", created_at="2026-11-13T00:00:00Z",
    )


def _strokes(points, pressure=None):
    return StrokeData(contours=[Contour(points=[StrokePoint(x=x, y=y, pressure=pressure) for x, y in points])])


def _codes(violations):
    return {v.split(":")[0] for v in violations}


def test_constants():
    assert EM_CONVENTION_VERSION == 1
    assert RASTER_PAD == 0.05


def test_conforming_sample_has_no_violations():
    strokes = _strokes([(0.05, 0.0), (0.55, 0.5)])
    assert check_sample_em(_sample(), strokes) == []


def test_none_strokes_skips_stroke_invariants():
    assert check_sample_em(_sample(), None) == []


def test_y_down_strokes_are_flagged_as_bbox_mismatch_and_point_range():
    strokes = _strokes([(0.05, 0.0), (0.55, -0.5)])  # y flipped
    assert _codes(check_sample_em(_sample(), strokes)) == {"bbox_mismatch"}
    far = _strokes([(0.05, 0.0), (0.55, -0.7)])
    assert "point_range" in _codes(check_sample_em(_sample(), far))


def test_metric_ranges():
    assert _codes(check_sample_em(_sample(baseline=0.2), None)) == {"baseline_range"}
    assert _codes(check_sample_em(_sample(baseline=-0.2), None)) == {"baseline_range"}
    assert _codes(check_sample_em(_sample(x_height=1.0), None)) == {"x_height_range"}
    assert _codes(check_sample_em(_sample(advance=2.6), None)) == {"advance_range"}
    assert check_sample_em(_sample(baseline=0.1), None) == []
    assert check_sample_em(_sample(advance=2.5), None) == []


def test_bbox_tolerance_is_0_005_em_per_edge():
    ok = _strokes([(0.054, 0.004), (0.546, 0.496)])
    assert check_sample_em(_sample(), ok) == []
    off = _strokes([(0.05, 0.0), (0.556, 0.5)])  # xmax off by 0.006
    assert _codes(check_sample_em(_sample(), off)) == {"bbox_mismatch"}


def test_mixed_pressure_is_flagged():
    strokes = StrokeData(contours=[Contour(points=[StrokePoint(x=0.05, y=0.0, pressure=0.5), StrokePoint(x=0.55, y=0.5)])])
    assert _codes(check_sample_em(_sample(), strokes)) == {"pressure_mixed"}
    all_float = _strokes([(0.05, 0.0), (0.55, 0.5)], pressure=0.5)
    assert check_sample_em(_sample(), all_float) == []


def _png(width, height):
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_png_size_reads_ihdr():
    assert png_size(_png(154, 154)) == (154, 154)
    with pytest.raises(ValueError):
        png_size(b"notapng")


def test_raster_size_consistent_with_padded_bbox_is_clean():
    # bbox 0.5 x 0.5 em, pad 0.05 each side -> 0.6 x 0.6 em at S=256 -> 154 x 154 px (153.6 rounded)
    assert check_sample_em(_sample(), None, raster=_png(154, 154)) == []
    assert check_sample_em(_sample(), None, raster=(154, 154)) == []


def test_raster_scale_mismatch_beyond_2_percent():
    assert _codes(check_sample_em(_sample(), None, raster=_png(154, 160))) == {"raster_scale_mismatch"}
    assert _codes(check_sample_em(_sample(), None, raster=(154, 160))) == {"raster_scale_mismatch"}
    assert check_sample_em(_sample(), None, raster=(154, 156)) == []  # 1.3 % -> fine


def test_raster_resolution_floor_32_px_per_em():
    assert _codes(check_sample_em(_sample(), None, raster=(18, 18))) == {"raster_resolution"}  # 30 px/em
    assert check_sample_em(_sample(), None, raster=(20, 20)) == []  # 33.3 px/em


def test_raster_pixel_em_round_trip_with_pad():
    bbox = BBox(x=0.05, y=-0.2, w=0.5, h=0.7)
    scale = 256.0
    assert raster_px_to_em(0.0, 0.0, bbox, scale) == pytest.approx((0.0, 0.55), abs=1e-9)  # top-left = (bbox.x - pad, bbox.y + h + pad)
    x, y = raster_em_to_px(0.3, 0.1, bbox, scale)
    assert raster_px_to_em(x, y, bbox, scale) == pytest.approx((0.3, 0.1), abs=1e-9)


def test_width_must_be_in_0_to_0_2_em():
    def with_width(w):
        return StrokeData(contours=[Contour(points=[StrokePoint(x=0.05, y=0.0, width=w), StrokePoint(x=0.55, y=0.5, width=w)])])
    assert check_sample_em(_sample(), with_width(0.05)) == []
    assert check_sample_em(_sample(), with_width(0.2)) == []
    assert _codes(check_sample_em(_sample(), with_width(0.0))) == {"width_range"}
    assert _codes(check_sample_em(_sample(), with_width(0.25))) == {"width_range"}

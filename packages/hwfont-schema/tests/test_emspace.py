from hwfont_schema import BBox, Context, Contour, Kind, Metrics, PositionInWord, Quality, ReviewStatus, Sample, StrokeData, StrokePoint
from hwfont_schema.emspace import EM_CONVENTION_VERSION, RASTER_PAD, check_sample_em


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

import pytest
from pydantic import ValidationError

from hwfont_schema.strokes import Contour, Outline, StrokeData, StrokePoint


def test_stroke_point_pressure_and_width_optional():
    p = StrokePoint(x=1.0, y=2.0)
    assert p.pressure is None and p.width is None


def test_outline_requires_a_ring_and_defaults_to_nonzero():
    o = Outline(rings=[[StrokePoint(x=0, y=0), StrokePoint(x=1, y=0), StrokePoint(x=1, y=1)]])
    assert o.fill_rule == "nonzero"
    with pytest.raises(ValidationError):
        Outline(rings=[])
    with pytest.raises(ValidationError):
        Outline(rings=[[StrokePoint(x=0, y=0)]], fill_rule="winding")


def test_stroke_data_round_trips_outlines_and_widths():
    data = StrokeData(
        contours=[Contour(points=[StrokePoint(x=0, y=0, width=3.0), StrokePoint(x=1, y=1, width=2.5)])],
        outlines=[Outline(rings=[[StrokePoint(x=0, y=0), StrokePoint(x=2, y=0), StrokePoint(x=2, y=2)]], fill_rule="evenodd")],
    )
    restored = StrokeData.model_validate_json(data.model_dump_json())
    assert restored == data
    assert StrokeData(contours=data.contours).outlines is None


def test_contour_requires_at_least_two_points():
    with pytest.raises(ValidationError):
        Contour(points=[StrokePoint(x=0, y=0)])


def test_stroke_data_requires_at_least_one_contour():
    with pytest.raises(ValidationError):
        StrokeData(contours=[])


def test_stroke_data_round_trips_through_json():
    data = StrokeData(
        contours=[
            Contour(points=[StrokePoint(x=0, y=0, pressure=0.5), StrokePoint(x=1, y=1)])
        ]
    )
    restored = StrokeData.model_validate_json(data.model_dump_json())
    assert restored == data

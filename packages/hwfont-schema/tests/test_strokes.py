import pytest
from pydantic import ValidationError

from hwfont_schema.strokes import Contour, StrokeData, StrokePoint


def test_stroke_point_pressure_optional():
    p = StrokePoint(x=1.0, y=2.0)
    assert p.pressure is None


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

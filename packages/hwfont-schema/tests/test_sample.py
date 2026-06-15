import pytest
from pydantic import ValidationError

from hwfont_schema.enums import Kind, PositionInWord, Quality, ReviewStatus
from hwfont_schema.geometry import BBox
from hwfont_schema.sample import Context, Metrics, Sample, Target


def _sample(**overrides) -> Sample:
    base = dict(
        id="s1",
        label="a",
        kind=Kind.single,
        strokes_path="strokes/s1.json",
        raster_path="raster/s1.png",
        context=Context(
            source_word="cat",
            left_neighbor="c",
            right_neighbor="t",
            position_in_word=PositionInWord.medial,
        ),
        metrics=Metrics(baseline=0.0, x_height=0.5, advance=0.6, bbox=BBox(x=0, y=0, w=0.5, h=0.5)),
        quality=Quality.good,
        review_status=ReviewStatus.accepted,
        capture_session_id="sess-1",
        created_at="2026-06-15T00:00:00Z",
    )
    base.update(overrides)
    return Sample(**base)


def test_sample_round_trips_through_json():
    s = _sample()
    restored = Sample.model_validate_json(s.model_dump_json())
    assert restored == s


def test_metrics_rejects_nonpositive_advance():
    with pytest.raises(ValidationError):
        Metrics(baseline=0.0, x_height=0.5, advance=0.0, bbox=BBox(x=0, y=0, w=1, h=1))


def test_target_requires_positive_required_count():
    with pytest.raises(ValidationError):
        Target(label="eft", kind=Kind.ligature, required_count=0)


def test_enums_serialize_to_their_string_values():
    s = _sample(review_status=ReviewStatus.needs_review)
    assert '"review_status":"needs_review"' in s.model_dump_json()


def test_metrics_rejects_nonpositive_x_height():
    with pytest.raises(ValidationError):
        Metrics(baseline=0.0, x_height=0.0, advance=0.6, bbox=BBox(x=0, y=0, w=1, h=1))

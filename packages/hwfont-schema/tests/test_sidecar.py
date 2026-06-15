import pytest
from pydantic import ValidationError

from hwfont_schema.geometry import BBox
from hwfont_schema.sidecar import CaptureSidecar, Page, Region


def _region() -> Region:
    return Region(
        id="r1",
        expected_transcript="the quick brown fox",
        baseline_y=120.0,
        bbox=BBox(x=0, y=80, w=600, h=60),
        expected_units=["t", "h", "e", "q", "u", "i", "c", "k"],
        ligature_targets=["ck"],
    )


def test_region_requires_at_least_one_expected_unit():
    with pytest.raises(ValidationError):
        Region(
            id="r1",
            expected_transcript="x",
            baseline_y=0.0,
            bbox=BBox(x=0, y=0, w=1, h=1),
            expected_units=[],
        )


def test_page_rejects_nonpositive_dimensions():
    with pytest.raises(ValidationError):
        Page(id="p1", width_px=0, height_px=100, dpi=300)


def test_sidecar_defaults_version_and_round_trips():
    sidecar = CaptureSidecar(
        pages=[Page(id="p1", width_px=1404, height_px=1872, dpi=226, regions=[_region()])]
    )
    assert sidecar.version == "1"
    restored = CaptureSidecar.model_validate_json(sidecar.model_dump_json())
    assert restored == sidecar

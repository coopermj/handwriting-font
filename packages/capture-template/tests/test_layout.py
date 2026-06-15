import pytest
from hwfont_schema import Kind, Target

from capture_template.layout import PageConfig, build_layout, rows_per_page
from capture_template.planner import PromptLine


def _config(**kw) -> PageConfig:
    base = dict(
        width_px=1000,
        height_px=1400,
        dpi=226,
        margin_px=50,
        prompt_font_px=24,
        prompt_gap_px=10,
        line_height_px=60,
        row_pitch_px=130,
    )
    base.update(kw)
    return PageConfig(**base)


def _targets():
    return [
        Target(label="c", kind=Kind.single, required_count=1),
        Target(label="a", kind=Kind.single, required_count=1),
        Target(label="t", kind=Kind.single, required_count=1),
        Target(label="at", kind=Kind.ligature, required_count=1),
    ]


def test_rows_per_page_floor_of_usable_height():
    # usable height = 1400 - 2*50 = 1300; 1300 / 130 = 10
    assert rows_per_page(_config()) == 10


def test_build_layout_geometry_and_pagination():
    lines = [PromptLine(text="cat", is_drill=False) for _ in range(12)]
    model = build_layout(lines, _targets(), _config())
    # 12 rows, 10 per page -> 2 pages (10 + 2)
    assert [len(p.rows) for p in model.pages] == [10, 2]

    row0 = model.pages[0].rows[0]
    # bbox: x=margin, y=margin+prompt_font+gap, w=width-2*margin, h=line_height
    assert (row0.bbox.x, row0.bbox.w, row0.bbox.h) == (50, 900, 60)
    assert row0.bbox.y == 50 + 24 + 10  # 84
    assert row0.baseline_y == row0.bbox.y + 60  # 144, inside [bbox.y, bbox.y+h]
    assert row0.expected_transcript == "cat"
    assert row0.expected_units == ["c", "a", "t"]  # only in-charset glyph labels, in order
    assert row0.ligature_targets == ["at"]

    # second row top advances by row_pitch
    row1 = model.pages[0].rows[1]
    assert row1.bbox.y == 50 + 130 + 24 + 10


def test_build_layout_rejects_row_that_does_not_fit_pitch():
    # prompt_font + gap + line_height = 24 + 10 + 60 = 94 must be <= row_pitch
    bad = _config(row_pitch_px=80)
    with pytest.raises(ValueError):
        build_layout([PromptLine(text="cat", is_drill=False)], _targets(), bad)

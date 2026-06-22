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
        max_line_chars=88,
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


def test_build_layout_rejects_margins_wider_than_page():
    bad = _config(width_px=80, margin_px=50)  # 2*50 >= 80 -> no usable width
    with pytest.raises(ValueError):
        build_layout([PromptLine(text="cat", is_drill=False)], _targets(), bad)


def test_build_layout_empty_lines_yields_no_pages():
    model = build_layout([], _targets(), _config())
    assert model.pages == []


def test_build_layout_exactly_one_full_page():
    lines = [PromptLine(text="cat", is_drill=False) for _ in range(10)]  # per_page == 10
    model = build_layout(lines, _targets(), _config())
    assert [len(p.rows) for p in model.pages] == [10]


def test_long_entry_wraps_into_consecutive_rows():
    cfg = _config(max_line_chars=12)  # _config passes kwargs to PageConfig
    # one entry that wraps to 3 segments at budget 12
    model = build_layout([PromptLine(text="the quick brown fox jumps over", is_drill=False)], _targets(), cfg)
    rows = [r for p in model.pages for r in p.rows]
    assert [r.prompt_text for r in rows] == ["the quick", "brown fox", "jumps over"]
    # each wrapped row is a normal row: its own transcript + units + bbox
    assert rows[0].expected_transcript == "the quick"
    assert rows[0].expected_units == ["t", "h", "e", "q", "u", "i", "c", "k"]


def test_wrapped_rows_paginate_across_pages():
    cfg = _config(max_line_chars=12)  # rows_per_page == 10 for _config
    # 4 entries each wrapping to 3 rows => 12 rows => 2 pages (10 + 2)
    entries = [PromptLine(text="the quick brown fox jumps over", is_drill=False) for _ in range(4)]
    model = build_layout(entries, _targets(), cfg)
    assert [len(p.rows) for p in model.pages] == [10, 2]


def test_layout_rejects_nonpositive_max_line_chars():
    with pytest.raises(ValueError):
        build_layout([PromptLine(text="cat", is_drill=False)], _targets(), _config(max_line_chars=0))


from capture_template.layout import PageConfig, fiducials


def _cfg(**overrides):
    base = dict(
        width_px=1404,
        height_px=1872,
        dpi=226,
        margin_px=80,
        prompt_font_px=28,
        prompt_gap_px=12,
        line_height_px=70,
        row_pitch_px=150,
    )
    base.update(overrides)
    return PageConfig(**base)


def test_fiducials_are_four_inset_corners():
    cfg = _cfg(fiducial_inset_px=40)
    marks = fiducials(cfg)
    assert [m.id for m in marks] == ["tl", "tr", "bl", "br"]
    by_id = {m.id: (m.x, m.y) for m in marks}
    assert by_id["tl"] == (40.0, 40.0)
    assert by_id["tr"] == (1404.0 - 40.0, 40.0)
    assert by_id["bl"] == (40.0, 1872.0 - 40.0)
    assert by_id["br"] == (1404.0 - 40.0, 1872.0 - 40.0)


def test_fiducial_radius_default():
    assert _cfg().fiducial_radius_px == 12

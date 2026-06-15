from hwfont_schema import CaptureSidecar, Kind, Target

from capture_template.layout import PageConfig, build_layout
from capture_template.planner import PromptLine
from capture_template.sidecar_out import build_sidecar


def _config() -> PageConfig:
    return PageConfig(
        width_px=1000,
        height_px=1400,
        dpi=226,
        margin_px=50,
        prompt_font_px=24,
        prompt_gap_px=10,
        line_height_px=60,
        row_pitch_px=130,
    )


def _targets():
    return [
        Target(label="c", kind=Kind.single, required_count=1),
        Target(label="a", kind=Kind.single, required_count=1),
        Target(label="t", kind=Kind.single, required_count=1),
        Target(label="at", kind=Kind.ligature, required_count=1),
    ]


def test_build_sidecar_matches_layout_and_validates():
    lines = [PromptLine(text="cat", is_drill=False), PromptLine(text="cat", is_drill=False)]
    model = build_layout(lines, _targets(), _config())
    sidecar = build_sidecar(model)

    # validates via hwfont-schema round-trip
    assert CaptureSidecar.model_validate_json(sidecar.model_dump_json()) == sidecar

    assert len(sidecar.pages) == 1
    page = sidecar.pages[0]
    assert (page.width_px, page.height_px, page.dpi) == (1000, 1400, 226)
    assert page.source_bounds is not None
    assert (page.source_bounds.w, page.source_bounds.h) == (1000, 1400)

    # one region per prompt line, geometry copied from the layout row
    assert len(page.regions) == 2
    region0 = page.regions[0]
    row0 = model.pages[0].rows[0]
    assert region0.expected_transcript == "cat"
    assert region0.expected_units == ["c", "a", "t"]
    assert region0.ligature_targets == ["at"]
    assert region0.baseline_y == row0.baseline_y
    assert region0.bbox == row0.bbox


def test_region_ids_are_unique():
    lines = [PromptLine(text="cat", is_drill=False) for _ in range(15)]  # spans 2 pages
    sidecar = build_sidecar(build_layout(lines, _targets(), _config()))
    ids = [r.id for p in sidecar.pages for r in p.regions]
    assert len(ids) == len(set(ids)) == 15

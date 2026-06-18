from __future__ import annotations

from dataclasses import dataclass, field

from hwfont_schema import BBox, Kind, Target

from capture_template.planner import PromptLine
from capture_template.text_wrap import wrap_text


@dataclass
class PageConfig:
    width_px: int
    height_px: int
    dpi: int
    margin_px: int
    prompt_font_px: int
    prompt_gap_px: int
    line_height_px: int
    row_pitch_px: int
    max_line_chars: int = 88


@dataclass
class Row:
    prompt_text: str
    expected_transcript: str
    expected_units: list[str]
    ligature_targets: list[str]
    bbox: BBox
    baseline_y: float


@dataclass
class LayoutPage:
    index: int
    rows: list[Row] = field(default_factory=list)


@dataclass
class LayoutModel:
    config: PageConfig
    pages: list[LayoutPage] = field(default_factory=list)


def rows_per_page(config: PageConfig) -> int:
    usable = config.height_px - 2 * config.margin_px
    return usable // config.row_pitch_px


def _validate(config: PageConfig) -> None:
    row_content = config.prompt_font_px + config.prompt_gap_px + config.line_height_px
    if row_content > config.row_pitch_px:
        raise ValueError(
            f"row content {row_content}px exceeds row_pitch_px {config.row_pitch_px}px"
        )
    if rows_per_page(config) < 1:
        raise ValueError(
            f"page too short: usable height {config.height_px - 2 * config.margin_px}px "
            f"< row_pitch_px {config.row_pitch_px}px"
        )
    usable_width = config.width_px - 2 * config.margin_px
    if usable_width <= 0:
        raise ValueError(
            f"margins ({config.margin_px}px each side) leave no usable width "
            f"on a {config.width_px}px page"
        )
    if config.max_line_chars < 1:
        raise ValueError(f"max_line_chars must be >= 1, got {config.max_line_chars}")


def _make_row(text: str, targets: list[Target], config: PageConfig, row_top: int) -> Row:
    ligature_labels = [t.label for t in targets if t.kind == Kind.ligature]
    bbox_y = row_top + config.prompt_font_px + config.prompt_gap_px
    bbox = BBox(
        x=float(config.margin_px),
        y=float(bbox_y),
        w=float(config.width_px - 2 * config.margin_px),
        h=float(config.line_height_px),
    )
    return Row(
        prompt_text=text,
        expected_transcript=text,
        expected_units=[ch for ch in text if not ch.isspace()],
        ligature_targets=[lig for lig in ligature_labels if lig in text],
        bbox=bbox,
        baseline_y=float(bbox_y + config.line_height_px),
    )


def build_layout(
    lines: list[PromptLine], targets: list[Target], config: PageConfig
) -> LayoutModel:
    _validate(config)
    per_page = rows_per_page(config)
    model = LayoutModel(config=config)
    row_index = 0
    for line in lines:
        for segment in wrap_text(line.text, config.max_line_chars):
            page_index = row_index // per_page
            row_in_page = row_index % per_page
            if row_in_page == 0:
                model.pages.append(LayoutPage(index=page_index))
            row_top = config.margin_px + row_in_page * config.row_pitch_px
            model.pages[page_index].rows.append(_make_row(segment, targets, config, row_top))
            row_index += 1
    return model

import pytest
from hwfont_schema import Kind, Target
from pypdf import PdfReader

from capture_template.layout import PageConfig, build_layout
from capture_template.pdf import px_to_pt, render_pdf
from capture_template.planner import PromptLine


def _config() -> PageConfig:
    return PageConfig(
        width_px=1000,
        height_px=1400,
        dpi=100,  # 100 dpi keeps px->pt math easy: 1000px -> 720pt
        margin_px=50,
        prompt_font_px=24,
        prompt_gap_px=10,
        line_height_px=60,
        row_pitch_px=130,
    )


def test_px_to_pt():
    assert px_to_pt(100, 100) == pytest.approx(72.0)
    assert px_to_pt(0, 226) == pytest.approx(0.0)


def test_render_pdf_writes_expected_page_count(tmp_path):
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    lines = [PromptLine(text="a cat", is_drill=False) for _ in range(15)]  # 2 pages
    model = build_layout(lines, targets, _config())
    out = tmp_path / "capture.pdf"
    render_pdf(model, out)
    assert out.exists()
    reader = PdfReader(str(out))
    assert len(reader.pages) == 2
    # page size in points matches px->pt of the configured page size
    media = reader.pages[0].mediabox
    assert float(media.width) == pytest.approx(720.0, abs=1.0)
    assert float(media.height) == pytest.approx(1008.0, abs=1.0)

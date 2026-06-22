import pytest
from hwfont_schema import Kind, Target
from pypdf import PdfReader

from capture_template.layout import LayoutModel, LayoutPage, PageConfig, build_layout
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


def _cfg_fid():
    return PageConfig(
        width_px=1404, height_px=1872, dpi=226, margin_px=80,
        prompt_font_px=28, prompt_gap_px=12, line_height_px=70,
        row_pitch_px=150, fiducial_inset_px=40, fiducial_radius_px=12,
    )


def test_pdf_draws_fiducial_marks(tmp_path):
    model = LayoutModel(config=_cfg_fid(), pages=[LayoutPage(index=0)])
    out = tmp_path / "capture.pdf"
    render_pdf(model, out)

    reader = PdfReader(str(out))
    content = reader.pages[0].get_contents().get_data().decode("latin-1")
    # filled fiducial circles emit Bézier curve ops ('c') and a fill (reportlab
    # uses the even-odd fill operator 'f*' for circle(fill=1))
    assert " c\n" in content or " c " in content
    assert "\nf*\n" in content or " f*\n" in content

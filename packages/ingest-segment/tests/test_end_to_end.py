from PIL import Image, ImageDraw

from capture_template.layout import LayoutModel, LayoutPage, PageConfig, build_layout
from capture_template.sidecar_out import build_sidecar
from capture_template.targets import default_targets
from ingest_segment.run import ingest_page
from ingest_segment.segment import VisionBox, VisionResult


def _small_config():
    return PageConfig(
        width_px=600, height_px=400, dpi=150, margin_px=40,
        prompt_font_px=18, prompt_gap_px=8, line_height_px=40,
        row_pitch_px=90, max_line_chars=40, fiducial_inset_px=30, fiducial_radius_px=8,
    )


def _raster_from_sidecar_page(page, radius=8):
    img = Image.new("L", (page.width_px, page.height_px), color=255)
    draw = ImageDraw.Draw(img)
    for f in page.fiducials:
        draw.ellipse([f.x - radius, f.y - radius, f.x + radius, f.y + radius], fill=0)
    return img


def test_capture_template_to_candidate_set(tmp_path):
    cfg = _small_config()
    # one real prompt line through the real layout + sidecar path
    from capture_template.planner import PromptLine

    model = build_layout([PromptLine(text="cat", is_drill=False)], default_targets(), cfg)
    sidecar = build_sidecar(model)
    page = sidecar.pages[0]
    assert len(page.fiducials) == 4  # fiducials flowed through Contract X

    raster = _raster_from_sidecar_page(page)
    region = page.regions[0]

    # synthesize one ink stroke inside the left third of the region
    bx, by, bw, bh = region.bbox.x, region.bbox.y, region.bbox.w, region.bbox.h
    stroke = [(bx + 5, by + 5), (bx + 8, by + bh - 5), (bx + 12, by + 10)]

    def vision(crop_png, r):
        third = bw / 3.0
        return VisionResult(boxes=[
            VisionBox(label="c", kind="single", x=0, y=0, w=third, h=bh, confidence=0.9),
            VisionBox(label="a", kind="single", x=third, y=0, w=third, h=bh, confidence=0.2),
            VisionBox(label="t", kind="single", x=2 * third, y=0, w=third, h=bh, confidence=0.8),
        ])

    out = tmp_path / "out"
    cs = ingest_page(
        page=page, raster=raster, export_size=raster.size,
        page_strokes_export=[stroke], vision=vision,
        model="claude-opus-4-8", created_at="2026-06-22T00:00:00Z", out_dir=out,
    )

    assert cs.provenance.alignment_method == "fiducial"
    assert cs.provenance.alignment_residual_px is not None
    # lowest-confidence-first
    confs = [c.confidence for c in cs.candidates]
    assert confs == sorted(confs)
    # low-confidence box flagged
    a = next(c for c in cs.candidates if c.label == "a")
    assert a.status.value == "needs_review"
    # context derived from the known transcript
    c0 = next(c for c in cs.candidates if c.label == "c")
    assert c0.context.source_word == "cat"
    # the synthesized stroke landed in the 'c' candidate
    assert c0.strokes_path is not None

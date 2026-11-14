"""Render a capture page as a PNG raster (the template layer, no ink).

The PDF is what gets written on; this raster is the same page in Contract X
page-pixel space, for ingest paths that need a template image but don't get one
from the device (native .rm strokes), for synthetic test exports, and for previews.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from hwfont_schema import Page

from capture_template.layout import LayoutModel, fiducials
from capture_template.sidecar_out import build_sidecar

_RULE_GRAY = 204     # 0.8 in the PDF
_PROMPT_GRAY = 153   # 0.6 in the PDF
_DEFAULT_PROMPT_FONT_PX = 28
_DEFAULT_PROMPT_GAP_PX = 12
_DEFAULT_FIDUCIAL_RADIUS_PX = 12


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow: fixed-size bitmap font
        return ImageFont.load_default()


def render_page_raster(
    page: Page,
    prompt_font_px: int = _DEFAULT_PROMPT_FONT_PX,
    prompt_gap_px: int = _DEFAULT_PROMPT_GAP_PX,
    fiducial_radius_px: int = _DEFAULT_FIDUCIAL_RADIUS_PX,
    prompts: bool = True,
) -> Image.Image:
    """Draw a sidecar Page (rules, faint prompts, fiducials) as an 8-bit grayscale image."""
    img = Image.new("L", (page.width_px, page.height_px), color=255)
    draw = ImageDraw.Draw(img)
    font = _font(prompt_font_px)
    for region in page.regions:
        b = region.bbox
        draw.line([(b.x, region.baseline_y), (b.x + b.w, region.baseline_y)], fill=_RULE_GRAY, width=1)
        if prompts:
            # the PDF sets the prompt's baseline `prompt_gap_px` above the writing bbox
            draw.text(
                (b.x, b.y - prompt_gap_px), region.expected_transcript,
                fill=_PROMPT_GRAY, font=font, anchor="ls",
            )
    for f in page.fiducials:
        r = fiducial_radius_px
        draw.ellipse([f.x - r, f.y - r, f.x + r, f.y + r], fill=0)
    return img


def render_model_rasters(model: LayoutModel, out_dir: str | Path) -> list[Path]:
    """Write one `page-<n>.png` (1-based, matching the reMarkable's export naming) per page."""
    out_dir = Path(out_dir)
    cfg = model.config
    sidecar = build_sidecar(model)
    written: list[Path] = []
    for i, page in enumerate(sidecar.pages, start=1):
        assert page.fiducials == fiducials(cfg)
        img = render_page_raster(
            page,
            prompt_font_px=cfg.prompt_font_px,
            prompt_gap_px=cfg.prompt_gap_px,
            fiducial_radius_px=cfg.fiducial_radius_px,
        )
        path = out_dir / f"page-{i}.png"
        img.save(path)
        written.append(path)
    return written

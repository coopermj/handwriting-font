"""Visual debug output: an overlay of what the adapter and aligner produced on a page.

This is how skeleton fidelity and alignment get judged on real pages without spending
on vision: ink stays as the page raster shows it, centerlines go on top in red
(orange when the stroke lost skeleton coverage), region boxes in blue, candidate
boxes in green (magenta when flagged needs_review), expected fiducials as blue
crosshairs and measured ones as cyan dots.
"""

from __future__ import annotations

from collections.abc import Iterable

from PIL import Image, ImageDraw, ImageFont

from hwfont_schema import Candidate, CandidateStatus, Fiducial, Region
from ingest_segment.remarkable_svg import PenStroke

_REGION = (40, 90, 220)
_FIDUCIAL = (40, 90, 220)
_MEASURED = (0, 190, 200)
_CENTERLINE = (220, 30, 30)
_LOW_COVERAGE = (240, 140, 0)
_CANDIDATE = (0, 150, 60)
_NEEDS_REVIEW = (200, 0, 180)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow
        return ImageFont.load_default()


def render_overlay(
    raster: Image.Image,
    strokes: Iterable[PenStroke] = (),
    regions: Iterable[Region] = (),
    candidates: Iterable[Candidate] = (),
    fiducials: Iterable[Fiducial] = (),
    measured: dict[str, tuple[float, float]] | None = None,
) -> Image.Image:
    """Draw strokes/regions/candidates/fiducials over a (page-space) raster; returns RGB."""
    img = Image.blend(raster.convert("RGB"), Image.new("RGB", raster.size, (255, 255, 255)), 0.35)
    draw = ImageDraw.Draw(img)
    font = _font(14)

    for region in regions:
        b = region.bbox
        draw.rectangle([b.x, b.y, b.x + b.w, b.y + b.h], outline=_REGION, width=1)
        draw.text((b.x + 2, b.y - 16), region.id, fill=_REGION, font=font)

    for f in fiducials:
        draw.line([(f.x - 12, f.y), (f.x + 12, f.y)], fill=_FIDUCIAL, width=1)
        draw.line([(f.x, f.y - 12), (f.x, f.y + 12)], fill=_FIDUCIAL, width=1)
    for (mx, my) in (measured or {}).values():
        draw.ellipse([mx - 3, my - 3, mx + 3, my + 3], fill=_MEASURED)

    for stroke in strokes:
        color = _LOW_COVERAGE if stroke.low_coverage else _CENTERLINE
        for line in stroke.centerlines:
            if len(line) >= 2:
                draw.line(line, fill=color, width=1)
            else:
                draw.point(line, fill=color)

    for c in candidates:
        b = c.bbox
        color = _NEEDS_REVIEW if c.status == CandidateStatus.needs_review else _CANDIDATE
        draw.rectangle([b.x, b.y, b.x + b.w, b.y + b.h], outline=color, width=1)
        tag = c.label if not c.review_reasons else f"{c.label} ({','.join(c.review_reasons)})"
        draw.text((b.x, b.y + b.h + 1), tag, fill=color, font=font)
    return img

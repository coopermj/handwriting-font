"""Raster fallback: reviewed PNG -> Outline via scikit-image contours (tier D)."""
from __future__ import annotations

import io

import numpy as np
import pathops
from hwfont_schema import BBox, Metrics
from hwfont_schema.emspace import RASTER_PAD, png_size, raster_px_to_em
from PIL import Image

from font_gen.centerline import SMOOTH_SIGMA, resample, smooth
from font_gen.nib import NIB
from font_gen.outline import Outline, OutlineResult, check_outline, fit_outline
from font_gen.qa import Finding

QU2CU_ERR_RASTER = 1.5
THRESHOLD_FALLBACK = 128


def skimage_available() -> bool:
    try:
        import skimage.filters  # noqa: F401
        import skimage.measure  # noqa: F401
    except ImportError:
        return False
    return True


def ink_mask(png: bytes) -> np.ndarray:
    gray = np.asarray(Image.open(io.BytesIO(png)).convert("L"))
    if skimage_available():
        from skimage.filters import threshold_otsu

        if gray.min() == gray.max():
            return gray < THRESHOLD_FALLBACK
        # skimage's convention: background is `<= t`, so a pure black/white image (t = 0) keeps its ink
        return gray <= threshold_otsu(gray)
    return gray < THRESHOLD_FALLBACK


def raster_polygons(mask: np.ndarray) -> list[np.ndarray]:
    """Closed ink contours as (col, row) arrays; the mask is padded so ink touching the edge still closes."""
    from skimage.measure import find_contours

    padded = np.pad(mask.astype(float), 1, constant_values=0.0)
    polygons = []
    for c in find_contours(padded, level=0.5):
        pts = c[:, ::-1] - 1.0
        if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
        if len(pts) >= 3:
            polygons.append(pts)
    return polygons


def georeference(polygon_px: np.ndarray, bbox: BBox, scale: float, baseline: float) -> np.ndarray:
    out = np.empty((len(polygon_px), 2), dtype=float)
    for i, (col, row) in enumerate(polygon_px):
        x, y = raster_px_to_em(float(col), float(row), bbox, scale)
        out[i] = (x * 1000.0, (y - baseline) * 1000.0)
    return out


def raster_to_outline(
    raster: bytes,
    metrics: Metrics,
    *,
    nib: float = NIB,
    smooth_sigma: float = SMOOTH_SIGMA,
    label: str | None = None,
    sample_id: str | None = None,
    glyph: str | None = None,
) -> OutlineResult:
    ctx = {"label": label, "sample_id": sample_id, "glyph": glyph}
    if not skimage_available():
        finding = Finding("warn", "raster_unavailable", "scikit-image is not installed; empty glyph emitted", **ctx)
        return OutlineResult(outline=Outline.empty(), findings=(finding,), tier="D", route="empty")
    bbox = metrics.bbox
    width_px, _ = png_size(raster)
    scale = width_px / (bbox.w + 2 * RASTER_PAD)
    path = pathops.Path()
    pen = path.getPen()
    for polygon in raster_polygons(ink_mask(raster)):
        pts = georeference(polygon, bbox, scale, metrics.baseline)
        pts = smooth(resample(pts, closed=True), smooth_sigma * nib, closed=True)
        pen.moveTo(tuple(pts[0]))
        for p in pts[1:]:
            pen.lineTo(tuple(p))
        pen.closePath()
    path.simplify(fix_winding=True, keep_starting_points=False, clockwise=False)
    outline, route = fit_outline(path, max_err=QU2CU_ERR_RASTER)
    findings = [Finding("warn", "raster_fallback", "no usable strokes; outline traced from the raster", **ctx)]
    findings.extend(check_outline(outline, metrics, nib, **ctx))
    return OutlineResult(outline=outline, findings=tuple(findings), tier="D", route=route)

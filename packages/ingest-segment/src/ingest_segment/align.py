from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from PIL import Image

from hwfont_schema import Fiducial, Page
from ingest_segment.remarkable_svg import PenStroke

# 2x3 affine: [[a, b, tx], [c, d, ty]] mapping (x, y, 1) -> (x', y')
Affine = np.ndarray


def estimate_affine(
    measured: dict[str, tuple[float, float]], expected: list[Fiducial]
) -> Affine:
    """Least-squares 2x3 affine mapping measured fiducial positions onto expected ones.

    Only ids present in both `measured` and `expected` are used. Raises ValueError
    if fewer than 3 correspondences (an affine needs 3 non-collinear points).
    """
    pairs = [(measured[f.id], (f.x, f.y)) for f in expected if f.id in measured]
    if len(pairs) < 3:
        raise ValueError(f"need >=3 fiducial correspondences, got {len(pairs)}")
    src = np.array([[mx, my, 1.0] for (mx, my), _ in pairs])
    dst = np.array([[ex, ey] for _, (ex, ey) in pairs])
    # solve src @ P = dst  for P (3x2); affine rows are P transposed
    p, *_ = np.linalg.lstsq(src, dst, rcond=None)
    return p.T  # shape (2, 3)


def apply_affine(matrix: Affine, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Apply a 2x3 affine to a list of (x, y) points."""
    if not points:
        return []
    pts = np.array([[x, y, 1.0] for x, y in points])
    out = pts @ matrix.T  # (n, 2)
    return [(float(x), float(y)) for x, y in out]


def invert_affine(matrix: Affine) -> Affine:
    """The 2x3 inverse of a 2x3 affine."""
    full = np.vstack([matrix, [0.0, 0.0, 1.0]])
    return np.linalg.inv(full)[:2]


def affine_scale(matrix: Affine) -> float:
    """Mean linear scale factor of an affine (sqrt of |det| of its 2x2 part)."""
    return float(np.sqrt(abs(np.linalg.det(matrix[:, :2]))))


def is_identity(matrix: Affine, tol: float = 1e-6) -> bool:
    return bool(np.allclose(matrix, np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), atol=tol))


def transform_stroke(matrix: Affine, stroke: PenStroke) -> PenStroke:
    """Map a stroke's centerlines and outline through an affine; widths scale with it."""
    s = affine_scale(matrix)
    return replace(
        stroke,
        centerlines=[apply_affine(matrix, line) for line in stroke.centerlines],
        widths=[[w * s for w in ws] for ws in stroke.widths],
        outline=[apply_affine(matrix, ring) for ring in stroke.outline],
    )


def warp_raster(raster: Image.Image, matrix: Affine, page_size: tuple[int, int]) -> Image.Image:
    """Resample an export-space raster into page-pixel space via the export->page affine.

    Region crops index the raster with sidecar (page-pixel) coordinates, so the raster
    must live in that space too, not only the strokes. An identity affine on a raster
    of the right size is returned unchanged (no resampling blur).
    """
    if is_identity(matrix) and raster.size == tuple(page_size):
        return raster
    inv = invert_affine(matrix)  # PIL wants output px -> input px
    (a, b, c), (d, e, f) = inv
    return raster.transform(
        tuple(page_size), Image.AFFINE, data=(a, b, c, d, e, f), resample=Image.BILINEAR, fillcolor=255
    )


def residual(
    matrix: Affine, measured: dict[str, tuple[float, float]], expected: list[Fiducial]
) -> float:
    """RMS reprojection error (pixels) of measured fiducials mapped onto expected."""
    ids = [f.id for f in expected if f.id in measured]
    src = [measured[i] for i in ids]
    proj = apply_affine(matrix, src)
    exp = {f.id: (f.x, f.y) for f in expected}
    errs = [
        (px - exp[i][0]) ** 2 + (py - exp[i][1]) ** 2
        for i, (px, py) in zip(ids, proj)
    ]
    return float(np.sqrt(np.mean(errs))) if errs else 0.0


_DEFAULT_RESIDUAL_THRESHOLD = 6.0


@dataclass
class Alignment:
    """The chosen export->page-pixel transform plus how it was derived."""

    matrix: Affine
    method: str  # "fiducial" | "geometric_scale"
    residual_px: float | None
    low_confidence: bool


def _geometric_scale(export_size: tuple[int, int], page: Page) -> Affine:
    ew, eh = export_size
    if ew <= 0 or eh <= 0:
        raise ValueError(f"invalid export size {export_size}")
    sx = page.width_px / ew
    sy = page.height_px / eh
    return np.array([[sx, 0.0, 0.0], [0.0, sy, 0.0]])


def align_page(
    measured: dict[str, tuple[float, float]],
    page: Page,
    export_size: tuple[int, int],
    residual_threshold: float = _DEFAULT_RESIDUAL_THRESHOLD,
) -> Alignment:
    """Pick an export->page-pixel affine: fiducials if usable, else geometric scale."""
    usable = [f for f in page.fiducials if f.id in measured]
    if len(usable) >= 3:
        matrix = estimate_affine(measured, page.fiducials)
        res = residual(matrix, measured, page.fiducials)
        return Alignment(
            matrix=matrix,
            method="fiducial",
            residual_px=res,
            low_confidence=res > residual_threshold,
        )
    # fallback: scale export dimensions onto the sidecar's page dimensions
    matrix = _geometric_scale(export_size, page)
    return Alignment(matrix=matrix, method="geometric_scale", residual_px=None, low_confidence=True)

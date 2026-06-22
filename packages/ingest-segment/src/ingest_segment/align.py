from __future__ import annotations

import numpy as np

from hwfont_schema import Fiducial

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

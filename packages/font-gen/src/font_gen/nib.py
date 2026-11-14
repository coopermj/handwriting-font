from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

import numpy as np
from hwfont_schema.strokes import Contour, StrokeData

UPM = 1000
NIB_EM = 0.045
NIB = NIB_EM * UPM
WIDTH_CLAMP = (0.4, 2.0)
PRESSURE_BASE = 70
PRESSURE_SPAN = 60
SLOPE_MAX = 0.7
RUN_TOL = 0.03

Tier = Literal["A", "B", "C"]


def nib_units(nib_em: float = NIB_EM) -> float:
    return nib_em * UPM


def sample_tier(strokes: StrokeData) -> tuple[Tier, bool]:
    points = [p for c in strokes.contours for p in c.points]
    for tier, attr in (("A", "width"), ("B", "pressure")):
        present = sum(getattr(p, attr) is not None for p in points)
        if present:
            return tier, present < len(points)
    return "C", False


def pressure_width(p: float, nib: float) -> float:
    return nib * (PRESSURE_BASE + PRESSURE_SPAN * p) / 100


def _fill_gaps(values: list[float | None], default: float) -> np.ndarray:
    known = [i for i, v in enumerate(values) if v is not None]
    if not known:
        return np.full(len(values), default, dtype=float)
    return np.interp(np.arange(len(values)), known, [values[i] for i in known])


def contour_widths(contour: Contour, tier: Tier, nib: float) -> np.ndarray:
    n = len(contour.points)
    if tier == "A":
        lo, hi = WIDTH_CLAMP[0] * nib, WIDTH_CLAMP[1] * nib
        raw = [None if p.width is None else min(max(p.width * UPM, lo), hi) for p in contour.points]
        return _fill_gaps(raw, nib)
    if tier == "B":
        raw = [None if p.pressure is None else pressure_width(p.pressure, nib) for p in contour.points]
        return _fill_gaps(raw, nib)
    return np.full(n, nib, dtype=float)


def gaussian_smooth(values: np.ndarray, sigma_samples: float, closed: bool) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if sigma_samples <= 0 or len(values) < 2:
        return values.copy()
    radius = math.ceil(3 * sigma_samples)
    offsets = np.arange(-radius, radius + 1)
    kernel = np.exp(-0.5 * (offsets / sigma_samples) ** 2)
    kernel /= kernel.sum()
    pad = [(radius, radius)] + [(0, 0)] * (values.ndim - 1)
    padded = np.pad(values, pad, mode="wrap" if closed else "reflect")
    n = len(values)
    out = np.zeros_like(values)
    for k, w in zip(offsets, kernel):
        out += w * padded[radius + k : radius + k + n]
    if not closed:
        out[0], out[-1] = values[0], values[-1]
    return out


def resample_widths(widths: np.ndarray, src_s: np.ndarray, dst_s: np.ndarray) -> np.ndarray:
    return np.interp(dst_s, src_s, widths)


def clamp_slope(widths: np.ndarray, step: float, max_slope: float = SLOPE_MAX) -> np.ndarray:
    out = np.asarray(widths, dtype=float).copy()
    limit = max_slope * step
    for i in range(len(out) - 1):
        out[i + 1] = min(out[i + 1], out[i] + limit)
    for i in range(len(out) - 2, -1, -1):
        out[i] = min(out[i], out[i + 1] + limit)
    return out


def split_runs(knot_widths: Sequence[float], tol: float = RUN_TOL) -> list[tuple[int, int]]:
    n = len(knot_widths)
    if n < 2:
        return []
    runs: list[tuple[int, int]] = []
    start = 0
    for i in range(1, n):
        first = knot_widths[start]
        if abs(knot_widths[i] - first) > tol * first:
            runs.append((start, i))
            start = i
    if start < n - 1:
        runs.append((start, n - 1))
    return runs

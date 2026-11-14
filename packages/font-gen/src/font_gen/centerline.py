from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pathops
from hwfont_schema.strokes import Contour

from font_gen.nib import gaussian_smooth

RESAMPLE = 8.0
SMOOTH_SIGMA = 0.25
DEDUPE = 0.5
DOT_FACTOR = 0.5
LOOP_GAP = 1.5
LOOP_MIN_LEN = 6.0
CR_TENSION = 1.0 / 6.0

PrepKind = Literal["degenerate", "dot", "open", "closed"]
Stub = tuple[tuple[float, float], tuple[float, float]]


@dataclass(frozen=True)
class Prepared:
    kind: PrepKind
    knots: np.ndarray
    knot_s: np.ndarray
    stub: Stub | None


def to_units(contour: Contour, baseline: float) -> np.ndarray:
    return np.array([[p.x * 1000, (p.y - baseline) * 1000] for p in contour.points], dtype=float)


def dedupe(points: np.ndarray, min_dist: float = DEDUPE) -> np.ndarray:
    points = np.asarray(points, dtype=float)
    if len(points) == 0:
        return points.reshape(0, 2)
    kept = [points[0]]
    for p in points[1:]:
        if np.linalg.norm(p - kept[-1]) >= min_dist:
            kept.append(p)
    return np.array(kept)


def arc_lengths(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=float)
    if len(points) == 0:
        return np.zeros(0)
    seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(seg)])


def polyline_length(points: np.ndarray) -> float:
    return float(arc_lengths(points)[-1]) if len(points) else 0.0


def is_dot(points: np.ndarray, nib: float) -> bool:
    return polyline_length(points) < DOT_FACTOR * nib


def dot_stub(points: np.ndarray) -> Stub:
    points = np.asarray(points, dtype=float)
    c = points.mean(axis=0)
    half = max(polyline_length(points), 1.0) / 2
    cov = np.cov(points, rowvar=False, bias=True) if len(points) > 1 else np.zeros((2, 2))
    if not np.any(cov):
        d = np.array([1.0, 0.0])
    else:
        _, vecs = np.linalg.eigh(cov)
        d = vecs[:, -1]
        along = float(np.dot(d, points[-1] - points[0]))
        if along < 0 or (along == 0 and (d[0] < 0 or (d[0] == 0 and d[1] < 0))):
            d = -d
    a = c - half * d
    b = c + half * d
    return ((float(a[0]), float(a[1])), (float(b[0]), float(b[1])))


def is_closed_loop(points: np.ndarray, nib: float) -> bool:
    points = np.asarray(points, dtype=float)
    gap = float(np.linalg.norm(points[-1] - points[0]))
    return gap < LOOP_GAP * nib and polyline_length(points) > LOOP_MIN_LEN * nib


def resample(points: np.ndarray, step: float = RESAMPLE, closed: bool = False) -> np.ndarray:
    points = np.asarray(points, dtype=float)
    if closed:
        ring = np.vstack([points, points[:1]])
        s = arc_lengths(ring)
        total = s[-1]
        n = max(3, math.floor(total / step + 0.5))
        targets = total * np.arange(n) / n
        src = ring
    else:
        s = arc_lengths(points)
        total = s[-1]
        n = max(2, math.floor(total / step + 0.5) + 1)
        targets = np.linspace(0.0, total, n)
        src = points
    return np.stack([np.interp(targets, s, src[:, 0]), np.interp(targets, s, src[:, 1])], axis=1)


def smooth(points: np.ndarray, sigma_units: float, step: float = RESAMPLE, closed: bool = False) -> np.ndarray:
    return gaussian_smooth(points, sigma_units / step, closed)


def catmull_rom_path(knots: np.ndarray, closed: bool = False, start: int = 0, end: int | None = None) -> pathops.Path:
    knots = np.asarray(knots, dtype=float)
    n = len(knots)
    path = pathops.Path()
    if n < 2:
        return path
    pen = path.getPen()
    if closed:
        if start != 0 or end is not None:
            raise ValueError("start/end select a knot range of an open path only")
        segments = [(knots[i - 1], knots[i], knots[(i + 1) % n], knots[(i + 2) % n]) for i in range(n)]
        first = knots[0]
    else:
        last = n - 1 if end is None else end
        if not 0 <= start < last <= n - 1:
            raise ValueError(f"invalid knot range {start}..{last} for {n} knots")
        segments = [
            (knots[i - 1] if i > 0 else knots[i], knots[i], knots[i + 1], knots[i + 2] if i + 2 < n else knots[i + 1])
            for i in range(start, last)
        ]
        first = knots[start]
    pen.moveTo((float(first[0]), float(first[1])))
    for p0, p1, p2, p3 in segments:
        c1 = p1 + (p2 - p0) * CR_TENSION
        c2 = p2 - (p3 - p1) * CR_TENSION
        pen.curveTo((float(c1[0]), float(c1[1])), (float(c2[0]), float(c2[1])), (float(p2[0]), float(p2[1])))
    if closed:
        pen.closePath()
    else:
        pen.endPath()
    return path


def prepare(points: np.ndarray, nib: float, smooth_sigma: float = SMOOTH_SIGMA, step: float = RESAMPLE) -> Prepared:
    pts = dedupe(points)
    if len(pts) < 2:
        return Prepared("degenerate", pts, arc_lengths(pts), None)
    if is_dot(pts, nib):
        stub = dot_stub(pts)
        knots = np.array(stub, dtype=float)
        return Prepared("dot", knots, arc_lengths(knots), stub)
    closed = is_closed_loop(pts, nib)
    res = resample(pts, step, closed)
    knot_s = arc_lengths(res)
    knots = smooth(res, smooth_sigma * nib, step, closed) if smooth_sigma > 0 else res
    return Prepared("closed" if closed else "open", knots, knot_s, None)

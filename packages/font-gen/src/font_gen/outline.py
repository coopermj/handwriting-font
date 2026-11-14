"""Stroked outlines: centerline pieces -> union -> rounded cubic `Outline`, with the self-intersection test,
fit fallbacks and degeneracy checks (spec §7.4–7.5 and Amendments 1–3)."""

from __future__ import annotations

import math
from dataclasses import dataclass
import numpy as np
import pathops
from fontTools.misc.roundTools import otRound
from fontTools.pens.areaPen import AreaPen
from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.qu2cuPen import Qu2CuPen
from fontTools.pens.recordingPen import RecordingPen
from hwfont_schema import Metrics
from hwfont_schema.strokes import StrokeData

from font_gen.centerline import RESAMPLE, SMOOTH_SIGMA, Prepared, arc_lengths, catmull_rom_path, prepare, to_units
from font_gen.nib import NIB, clamp_slope, contour_widths, gaussian_smooth, resample_widths, sample_tier, split_runs
from font_gen.qa import Finding

QU2CU_ERR = 1.0
FLATTEN_N = 8
INTERSECT_TOL = 0.5
DRIFT_TOL = 0.05
FIXED_POINT_ROUNDS = 3
MAX_CONTOURS = 12
TINY_FACTOR = 0.3
BBOX_TOL_UNITS = 50
BOUNDS_LIMITS = (-600, -700, 2500, 1400)

Point = tuple[int, int]
Segment = tuple[str, Point] | tuple[str, Point, Point, Point]


@dataclass(frozen=True)
class OutlineContour:
    start: Point
    segments: tuple[Segment, ...]

    def draw(self, pen) -> None:
        pen.moveTo(self.start)
        for seg in self.segments:
            if seg[0] == "line":
                pen.lineTo(seg[1])
            else:
                pen.curveTo(*seg[1:])
        pen.closePath()


@dataclass(frozen=True)
class Outline:
    contours: tuple[OutlineContour, ...]
    bounds: tuple[int, int, int, int] | None

    def draw(self, pen) -> None:
        for contour in self.contours:
            contour.draw(pen)

    def area(self) -> float:
        pen = AreaPen()
        self.draw(pen)
        return pen.value

    def contour_areas(self) -> list[float]:
        areas = []
        for contour in self.contours:
            pen = AreaPen()
            contour.draw(pen)
            areas.append(pen.value)
        return areas

    @staticmethod
    def empty() -> Outline:
        return Outline(contours=(), bounds=None)

    @staticmethod
    def from_recording(value, round_coords: bool = True) -> Outline:
        fix = (lambda p: (otRound(p[0]), otRound(p[1]))) if round_coords else (lambda p: (p[0], p[1]))
        contours: list[OutlineContour] = []
        start = None
        segments: list[Segment] = []
        for op, args in value:
            if op == "moveTo":
                start, segments = fix(args[0]), []
            elif op == "lineTo":
                segments.append(("line", fix(args[0])))
            elif op == "curveTo":
                if len(args) != 3:
                    raise ValueError(f"curveTo with {len(args)} points; expected one cubic per call")
                segments.append(("curve", fix(args[0]), fix(args[1]), fix(args[2])))
            elif op in ("closePath", "endPath"):
                if start is not None:
                    contours.append(OutlineContour(start=start, segments=tuple(segments)))
                start, segments = None, []
            else:
                raise ValueError(f"unsupported pen operation {op!r} in an outline recording")
        if not contours:
            return Outline.empty()
        outline = Outline(contours=tuple(contours), bounds=None)
        pen = BoundsPen(None)
        outline.draw(pen)
        b = pen.bounds
        bounds = None if b is None else (math.floor(b[0]), math.floor(b[1]), math.ceil(b[2]), math.ceil(b[3]))
        return Outline(contours=outline.contours, bounds=bounds)


def notdef_outline() -> Outline:
    value = [
        ("moveTo", ((50, 0),)), ("lineTo", ((450, 0),)), ("lineTo", ((450, 700),)), ("lineTo", ((50, 700),)), ("closePath", ()),
        ("moveTo", ((100, 50),)), ("lineTo", ((100, 650),)), ("lineTo", ((400, 650),)), ("lineTo", ((400, 50),)), ("closePath", ()),
    ]
    return Outline.from_recording(value)


def stroke_centerline(path: pathops.Path, width: float) -> pathops.Path:
    out = pathops.Path(path)
    out.stroke(width, pathops.LineCap.ROUND_CAP, pathops.LineJoin.ROUND_JOIN, 4.0)
    out.convertConicsToQuads()
    return out


def union_pieces(pieces: list[pathops.Path]) -> pathops.Path:
    out = pathops.Path()
    if not pieces:
        return out
    pathops.union(pieces, out.getPen())
    out.simplify(fix_winding=True, keep_starting_points=False, clockwise=False)
    return out


def quads_to_outline(path: pathops.Path, max_err: float = QU2CU_ERR) -> Outline:
    rec = RecordingPen()
    path.draw(Qu2CuPen(rec, max_err=max_err, all_cubic=True))
    return Outline.from_recording(rec.value)


class _ElevationPen(BasePen):
    """Records lines and cubics; every quadratic becomes its exact degree elevation."""

    def __init__(self) -> None:
        super().__init__(None)
        self.value: list = []

    def _moveTo(self, pt):
        self.value.append(("moveTo", (pt,)))

    def _lineTo(self, pt):
        self.value.append(("lineTo", (pt,)))

    def _curveToOne(self, pt1, pt2, pt3):
        self.value.append(("curveTo", (pt1, pt2, pt3)))

    def _qCurveToOne(self, pt1, pt2):
        p0 = self._getCurrentPoint()
        c1 = (p0[0] + 2 / 3 * (pt1[0] - p0[0]), p0[1] + 2 / 3 * (pt1[1] - p0[1]))
        c2 = (pt2[0] + 2 / 3 * (pt1[0] - pt2[0]), pt2[1] + 2 / 3 * (pt1[1] - pt2[1]))
        self.value.append(("curveTo", (c1, c2, pt2)))

    def _closePath(self):
        self.value.append(("closePath", ()))

    def _endPath(self):
        self.value.append(("endPath", ()))


def elevated_outline(path: pathops.Path) -> Outline:
    pen = _ElevationPen()
    path.draw(pen)
    return Outline.from_recording(pen.value)


def outline_to_path(outline: Outline) -> pathops.Path:
    path = pathops.Path()
    outline.draw(path.getPen())
    return path


def path_area(path: pathops.Path) -> float:
    return path.area


def flatten_outline(outline: Outline, n: int = FLATTEN_N, tol: float = INTERSECT_TOL) -> list[np.ndarray]:
    ts = np.arange(1, n + 1) / n
    polys = []
    for contour in outline.contours:
        start = np.array(contour.start, dtype=float)
        raw = [start]
        cur = start
        for seg in contour.segments:
            if seg[0] == "line":
                cur = np.array(seg[1], dtype=float)
                raw.append(cur)
            else:
                c1, c2, p = (np.array(q, dtype=float) for q in seg[1:])
                u = 1 - ts[:, None]
                t = ts[:, None]
                raw.extend(u ** 3 * cur + 3 * u * u * t * c1 + 3 * u * t * t * c2 + t ** 3 * p)
                cur = p
        kept = [start]
        for q in raw[1:]:
            if np.linalg.norm(q - kept[-1]) >= tol:
                kept.append(q)
        while len(kept) > 1 and np.linalg.norm(kept[-1] - start) < tol:
            kept.pop()
        kept.append(start)
        polys.append(np.array(kept))
    return polys


def _orient(p: np.ndarray, q: np.ndarray, r: np.ndarray) -> np.ndarray:
    return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])


def is_intersection_free(outline: Outline) -> bool:
    polys = [p for p in flatten_outline(outline) if len(p) >= 2]
    if not polys:
        return True
    a = np.concatenate([p[:-1] for p in polys])
    b = np.concatenate([p[1:] for p in polys])
    cid = np.concatenate([np.full(len(p) - 1, k) for k, p in enumerate(polys)])
    idx = np.concatenate([np.arange(len(p) - 1) for p in polys])
    count = np.concatenate([np.full(len(p) - 1, len(p) - 1) for p in polys])
    ai, bi, aj, bj = a[:, None], b[:, None], a[None], b[None]
    o1, o2 = _orient(ai, bi, aj), _orient(ai, bi, bj)
    o3, o4 = _orient(aj, bj, ai), _orient(aj, bj, bi)
    cross = (o1 * o2 < 0) & (o3 * o4 < 0)
    same = cid[:, None] == cid[None]
    gap = (idx[:, None] - idx[None]) % count[:, None]
    adjacent = same & ((gap == 1) | (gap == count[:, None] - 1))
    eq = lambda u, v: np.all(u == v, axis=-1)
    shared = eq(ai, aj) | eq(ai, bj) | eq(bi, aj) | eq(bi, bj)
    cross &= ~adjacent & ~shared
    np.fill_diagonal(cross, False)
    return not bool(cross.any())


def drift_ok(outline: Outline, union_path: pathops.Path) -> bool:
    union_area = path_area(union_path)
    if len(outline.contours) != len(list(union_path.contours)):
        return False
    return abs(outline.area() - union_area) <= DRIFT_TOL * abs(union_area)


def fit_outline(union_path: pathops.Path, max_err: float = QU2CU_ERR) -> tuple[Outline, str]:
    if not list(union_path.contours) or path_area(union_path) == 0.0:
        return Outline.empty(), "empty"
    candidate = quads_to_outline(union_path, max_err)
    if is_intersection_free(candidate) and drift_ok(candidate, union_path):
        return candidate, "qu2cu"
    candidate = elevated_outline(union_path)
    if is_intersection_free(candidate) and drift_ok(candidate, union_path):
        return candidate, "elevation"
    for _ in range(FIXED_POINT_ROUNDS):
        q = outline_to_path(candidate)
        q.simplify(fix_winding=True, keep_starting_points=True, clockwise=False)
        rec = RecordingPen()
        q.draw(rec)
        candidate = Outline.from_recording(rec.value)
        if is_intersection_free(candidate) and drift_ok(candidate, union_path):
            return candidate, "fixed_point"
    return candidate, "unstable"


def _stub_path(stub) -> pathops.Path:
    path = pathops.Path()
    path.moveTo(*stub[0])
    path.lineTo(*stub[1])
    return path


def stroke_prepared(prepared: Prepared, widths: np.ndarray | None, nib: float) -> list[pathops.Path]:
    if prepared.kind == "degenerate":
        return []
    if prepared.kind == "dot":
        w = nib if widths is None else float(np.mean(widths))
        return [stroke_centerline(_stub_path(prepared.stub), w)]
    closed = prepared.kind == "closed"
    if widths is None:
        return [stroke_centerline(catmull_rom_path(prepared.knots, closed=closed), nib)]
    knots = np.asarray(prepared.knots, dtype=float)
    widths = np.asarray(widths, dtype=float)
    if closed:
        knots = np.vstack([knots, knots[:1]])
        widths = np.append(widths, widths[0])
    runs = split_runs(widths)
    if closed and len(runs) == 1:
        return [stroke_centerline(catmull_rom_path(prepared.knots, closed=True), float(np.mean(widths[:-1])))]
    return [stroke_centerline(catmull_rom_path(knots, start=s, end=e), float(np.mean(widths[s : e + 1]))) for s, e in runs]


@dataclass(frozen=True)
class OutlineResult:
    outline: Outline
    findings: tuple[Finding, ...]
    tier: str
    route: str


def outline_from_strokes(
    strokes: StrokeData,
    metrics: Metrics,
    *,
    nib: float = NIB,
    smooth_sigma: float = SMOOTH_SIGMA,
    label: str | None = None,
    sample_id: str | None = None,
    glyph: str | None = None,
) -> OutlineResult:
    ctx = {"label": label, "sample_id": sample_id, "glyph": glyph}
    findings: list[Finding] = []
    tier, partial = sample_tier(strokes)
    if partial and tier == "B":
        findings.append(Finding("warn", "pressure_partial", "pressure is missing on some points; gaps interpolated", **ctx))
    pieces: list[pathops.Path] = []
    for k, contour in enumerate(strokes.contours):
        points = to_units(contour, metrics.baseline)
        prepared = prepare(points, nib, smooth_sigma)
        if prepared.kind == "degenerate":
            findings.append(Finding("info", "stroke_degenerate", f"stroke {k} has fewer than 2 distinct points; skipped",
                                    data={"contour": k}, **ctx))
            continue
        widths = None
        if tier != "C":
            raw = contour_widths(contour, tier, nib)
            if prepared.kind == "dot":
                widths = raw
            else:
                closed = prepared.kind == "closed"
                widths = resample_widths(raw, arc_lengths(points), prepared.knot_s)
                widths = gaussian_smooth(widths, smooth_sigma * nib / RESAMPLE, closed)
                widths = clamp_slope(widths, RESAMPLE)
        if prepared.kind == "dot":
            findings.append(Finding("info", "dot_rendered", f"stroke {k} rendered as a dot", data={"contour": k}, **ctx))
        pieces.extend(stroke_prepared(prepared, widths, nib))
    if not pieces:
        outline, route = Outline.empty(), "empty"
    else:
        outline, route = fit_outline(union_pieces(pieces))
        if route in ("elevation", "fixed_point"):
            findings.append(Finding("warn", "outline_fit_fallback", f"qu2cu outline failed its checks; shipped the {route} fallback",
                                    data={"route": route}, **ctx))
        elif route == "unstable":
            findings.append(Finding("warn", "outline_unstable", "simplify fixed point did not converge; last candidate shipped", **ctx))
        elif route == "empty":
            findings.append(Finding("warn", "outline_empty", "stroke union has zero area; empty glyph emitted", **ctx))
    findings.extend(check_outline(outline, metrics, nib, **ctx))
    return OutlineResult(outline=outline, findings=tuple(findings), tier=tier, route=route)


def check_outline(
    outline: Outline,
    metrics: Metrics,
    nib: float,
    *,
    label: str | None = None,
    sample_id: str | None = None,
    glyph: str | None = None,
) -> list[Finding]:
    ctx = {"label": label, "sample_id": sample_id, "glyph": glyph}
    tiny = (TINY_FACTOR * nib) ** 2
    if outline.bounds is None:
        return [Finding("warn", "outline_degenerate", "outline is empty; empty glyph emitted", **ctx)]
    findings: list[Finding] = []
    area = outline.area()
    if abs(area) < tiny:
        findings.append(Finding("warn", "outline_degenerate", f"outline area {area:.1f} is below {tiny:.1f}",
                                data={"area": area}, **ctx))
    for k, a in enumerate(outline.contour_areas()):
        if abs(a) < tiny:
            findings.append(Finding("info", "outline_tiny_contour", f"contour {k} area {a:.1f} is below {tiny:.1f}",
                                    data={"contour": k, "area": a}, **ctx))
    if len(outline.contours) > MAX_CONTOURS:
        findings.append(Finding("warn", "outline_fragmented", f"{len(outline.contours)} contours (> {MAX_CONTOURS})",
                                data={"contours": len(outline.contours)}, **ctx))
    x0, y0, x1, y1 = outline.bounds
    lx0, ly0, lx1, ly1 = BOUNDS_LIMITS
    if x0 < lx0 or y0 < ly0 or x1 > lx1 or y1 > ly1:
        findings.append(Finding("warn", "metrics_suspicious", f"outline bounds {outline.bounds} outside {BOUNDS_LIMITS}",
                                data={"bounds": list(outline.bounds)}, **ctx))
    bb, half = metrics.bbox, nib / 2
    expected = ((bb.x * 1000) - half, (bb.y - metrics.baseline) * 1000 - half,
                (bb.x + bb.w) * 1000 + half, (bb.y + bb.h - metrics.baseline) * 1000 + half)
    if any(abs(got - want) > BBOX_TOL_UNITS for got, want in zip(outline.bounds, expected)):
        findings.append(Finding("warn", "metrics_inconsistent", f"outline bounds {outline.bounds} differ from the stored bbox "
                                f"{tuple(round(v, 1) for v in expected)} by more than {BBOX_TOL_UNITS} units",
                                data={"bounds": list(outline.bounds), "expected": [round(v, 3) for v in expected]}, **ctx))
    return findings

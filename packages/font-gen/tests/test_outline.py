import inspect
import math

import numpy as np
import pathops
from fontTools.pens.areaPen import AreaPen
from fontTools.pens.basePen import decomposeQuadraticSegment
from fontTools.pens.recordingPen import RecordingPen
from hwfont_schema import BBox, Metrics
from hwfont_schema.strokes import Contour, StrokeData, StrokePoint

import font_gen.centerline
import font_gen.outline as outline_mod
from font_gen.centerline import RESAMPLE, catmull_rom_path, prepare
from font_gen.outline import (Outline, OutlineContour, check_outline, elevated_outline, fit_outline,
                              is_intersection_free, notdef_outline, outline_from_strokes, outline_to_path,
                              quads_to_outline, stroke_centerline, union_pieces)

NIB = 45.0
DISC = math.pi * 22.5 ** 2  # 1590.43
SHIP_TOL = 0.05  # Qu2CuPen(max_err=1) + integer rounding lose 2.6 % of a 45-unit stroke's area and 4.5 % of a dot's (measured)


def test_no_conic_to_anywhere():
    # skia-pathops 0.9.2 corrupts axis-aligned quarter arcs through Path.conicTo; the geometry modules never spell it
    # (test_autotrace repeats the check for autotrace.py, which does not exist yet at this task)
    src = "".join(inspect.getsource(m) for m in (font_gen.centerline, outline_mod))
    assert "conic" + "To" not in src


def _metrics(x=0.0, y=0.0, w=0.5, h=0.5, baseline=0.0, advance=0.6):
    return Metrics(baseline=baseline, x_height=0.45, advance=advance, bbox=BBox(x=x, y=y, w=w, h=h))


def _strokes(*contours):
    return StrokeData(contours=[Contour(points=[StrokePoint(x=x / 1000, y=y / 1000, **kw) for x, y, kw in c]) for c in contours])


def _pts(points, **kw):
    return [(x, y, kw) for x, y in points]


def _circle(r=200.0, n=36, cx=300.0, cy=300.0):
    return [(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def test_straight_tier_c_stroke_area_and_single_contour():
    res = outline_from_strokes(_strokes(_pts([(100, 100), (300, 100)])), _metrics(x=0.1, y=0.1, w=0.2, h=0.001))
    assert res.tier == "C" and res.route == "qu2cu" and len(res.outline.contours) == 1
    assert math.isclose(res.outline.area(), NIB * 200 + DISC, rel_tol=SHIP_TOL)   # measured ratio 0.974
    assert res.outline.area() < NIB * 200 + DISC                                    # the loss is always inward
    assert 77 <= res.outline.bounds[0] <= 78   # 100 - NIB/2, rounded


def test_dot_is_a_stroked_stub_disc_with_only_cubics():
    res = outline_from_strokes(_strokes(_pts([(100, 600), (104, 600)])), _metrics(x=0.1, y=0.6, w=0.004, h=0.001))
    # the 4-unit stub keeps its length (max(length, 1)), so the disc carries a 4 x NIB rectangle: 1770.4 analytic, 1691 shipped
    assert len(res.outline.contours) == 1 and math.isclose(res.outline.area(), DISC + NIB * 4, rel_tol=SHIP_TOL)
    rec = RecordingPen()
    res.outline.draw(rec)
    assert {op for op, _ in rec.value} <= {"moveTo", "curveTo", "lineTo", "closePath"}
    assert any(f.code == "dot_rendered" for f in res.findings)


def _height_at(outline, x):
    probe = pathops.Path()
    probe.moveTo(x - 0.5, -1000); probe.lineTo(x + 0.5, -1000); probe.lineTo(x + 0.5, 2000); probe.lineTo(x - 0.5, 2000); probe.close()
    hit = pathops.op(outline_to_path(outline), probe, pathops.PathOp.INTERSECTION)
    b = hit.bounds
    return b[3] - b[1]


def test_pressure_ramp_thickness_and_run_union():
    pts = [(100 + 20 * i, 100, {"pressure": i / 20}) for i in range(21)]  # 400 units, pressure 0 -> 1
    res = outline_from_strokes(_strokes(pts), _metrics(x=0.1, y=0.1, w=0.4, h=0.001))
    assert res.tier == "B" and len(res.outline.contours) == 1 and res.outline.area() > 0
    assert 30 <= _height_at(res.outline, 130) <= 37      # ~0.7 * NIB near the start
    assert 53 <= _height_at(res.outline, 470) <= 60      # ~1.3 * NIB near the end
    partial = _strokes([(100, 100, {"pressure": 0.5}), (300, 100, {})])
    assert any(f.code == "pressure_partial" and f.severity == "warn" for f in outline_from_strokes(partial, _metrics(x=0.1, y=0.1, w=0.2, h=0.001)).findings)


def test_crossing_strokes_merge_and_loop_has_two_contours_outer_ccw():
    plus = outline_from_strokes(_strokes(_pts([(100, 300), (500, 300)]), _pts([(300, 100), (300, 500)])), _metrics(x=0.1, y=0.1, w=0.4, h=0.4))
    assert len(plus.outline.contours) == 1 and plus.outline.area() > 0
    o = outline_from_strokes(_strokes(_pts(_circle())), _metrics(x=0.1, y=0.1, w=0.4, h=0.4))
    areas = sorted(o.outline.contour_areas(), key=abs)
    assert len(areas) == 2 and areas[-1] > 0 and areas[0] < 0


ARC = [(300 + 200 * math.cos(math.pi * k / 24), 300 + 200 * math.sin(math.pi * k / 24)) for k in range(25)]


def test_smooth_curve_has_no_long_line_segments():
    # facet check: stroking the raw polyline leaves 48 lines of up to 27 units on this arc (measured);
    # stroking the smoothed Catmull-Rom cubic leaves lines only where the offset is straight, i.e. at most one knot interval long
    res = outline_from_strokes(_strokes(_pts(ARC)), _metrics(x=0.1, y=0.3, w=0.4, h=0.2))
    for c in res.outline.contours:
        prev = c.start
        for seg in c.segments:
            if seg[0] == "line":
                assert math.dist(prev, seg[1]) <= 1.5 * RESAMPLE
            prev = seg[-1]


def _flatten_recording(value, n=16):
    """Dense polylines (one per contour) of a RecordingPen value holding lines, quads (TrueType-style) and cubics."""
    polys, cur = [], None
    for op, args in value:
        if op == "moveTo":
            cur = np.array(args[0], float); polys.append([cur])
        elif op == "lineTo":
            cur = np.array(args[0], float); polys[-1].append(cur)
        elif op == "qCurveTo":
            for q, p in decomposeQuadraticSegment(args):
                q, p = np.array(q, float), np.array(p, float)
                for t in np.linspace(0, 1, n + 1)[1:]:
                    polys[-1].append((1 - t) ** 2 * cur + 2 * (1 - t) * t * q + t * t * p)
                cur = p
        elif op == "curveTo":
            c1, c2, p = (np.array(a, float) for a in args)
            for t in np.linspace(0, 1, n + 1)[1:]:
                polys[-1].append((1 - t) ** 3 * cur + 3 * (1 - t) ** 2 * t * c1 + 3 * (1 - t) * t * t * c2 + t ** 3 * p)
            cur = p
    return [np.array(p + [p[0]]) for p in polys]


def _max_distance(points, polylines):
    """Largest distance from any of `points` to the nearest segment of any polyline."""
    best = np.full(len(points), np.inf)
    for poly in polylines:
        a, b = poly[:-1], poly[1:]
        ab = b - a
        t = ((points[:, None, :] - a[None]) * ab[None]).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-12)[None]
        proj = a[None] + np.clip(t, 0.0, 1.0)[..., None] * ab[None]
        best = np.minimum(best, np.linalg.norm(points[:, None, :] - proj, axis=-1).min(axis=1))
    return float(best.max())


def test_qu2cu_error_bound_against_the_quad_union():
    prepared = prepare(np.array(ARC), NIB)
    union = union_pieces([stroke_centerline(catmull_rom_path(prepared.knots), NIB)])
    outline = quads_to_outline(union)
    rec_u, rec_o = RecordingPen(), RecordingPen()
    union.draw(rec_u)
    outline.draw(rec_o)
    quad_polys = _flatten_recording(rec_u.value)
    cubic_points = np.concatenate(_flatten_recording(rec_o.value))
    assert _max_distance(cubic_points, quad_polys) <= 1.0 + math.sqrt(0.5)   # QU2CU_ERR plus the integer-rounding radius


def _square_path(points):
    p = pathops.Path()
    p.moveTo(*points[0])
    for pt in points[1:]:
        p.lineTo(*pt)
    p.close()
    return p


def _outline(points):
    return Outline.from_recording([("moveTo", (points[0],))] + [("lineTo", (pt,)) for pt in points[1:]] + [("closePath", ())])


SQUARE = [(0, 0), (100, 0), (100, 100), (0, 100)]
CORRUPT = [(0, 0), (100, 0), (100, 100), (60, 100), (40, 101), (61, 101), (39, 100), (0, 100)]   # a 1-unit crossing loop on the top edge


def test_self_intersection_test_is_exact_where_the_area_heuristic_is_not():
    square, corrupt = _outline(SQUARE), _outline(CORRUPT)
    assert is_intersection_free(square)
    assert not is_intersection_free(corrupt)
    assert abs(corrupt.area() - square.area()) / square.area() < 0.01   # the heuristic alone would pass it
    touching = _outline([(0, 0), (100, 0), (100, 100), (50, 100), (61, 101), (40, 101), (50, 100), (0, 100)])
    assert is_intersection_free(touching)                                # a pinched contour touches, it does not cross
    o = outline_from_strokes(_strokes(_pts(_circle())), _metrics(x=0.1, y=0.1, w=0.4, h=0.4))
    assert is_intersection_free(o.outline) and o.route == "qu2cu"        # ordinary glyphs ship on the first route


def test_elevation_fallback_is_exact_degree_elevation():
    p = pathops.Path()
    p.moveTo(0, 0); p.quadTo(50, 100, 100, 0); p.close()
    out = elevated_outline(p)
    assert out.contours[0].segments[0] == ("curve", (33, 67), (67, 67), (100, 0))


def test_fallback_order_elevation_then_simplify_fixed_point(monkeypatch):
    square = _square_path(SQUARE)
    corrupt = _outline(CORRUPT)
    monkeypatch.setattr(outline_mod, "quads_to_outline", lambda path, max_err=1.0: corrupt)
    outline, route = fit_outline(square)
    assert route == "elevation" and is_intersection_free(outline) and outline == _outline(SQUARE)
    monkeypatch.setattr(outline_mod, "elevated_outline", lambda path: corrupt)
    outline, route = fit_outline(square)
    # simplify(keep_starting_points=True) resolves the crossing into a pinched vertex at (50, 100): crossing-free, area 10010.5
    assert route == "fixed_point" and is_intersection_free(outline) and len(outline.contours) == 1
    assert fit_outline(pathops.Path()) == (Outline.empty(), "empty")


def test_fit_routes_become_findings(monkeypatch):
    strokes = _strokes(_pts([(100, 100), (300, 100)]))
    m = _metrics(x=0.1, y=0.1, w=0.2, h=0.001)
    real = outline_mod.fit_outline
    for route, code in (("elevation", "outline_fit_fallback"), ("fixed_point", "outline_fit_fallback"),
                        ("unstable", "outline_unstable"), ("empty", "outline_empty")):
        monkeypatch.setattr(outline_mod, "fit_outline", lambda path, max_err=1.0, r=route: (real(path, max_err)[0] if r != "empty" else Outline.empty(), r))
        res = outline_from_strokes(strokes, m)
        f = next(f for f in res.findings if f.code == code)
        assert res.route == route and f.severity == "warn"
        if code == "outline_fit_fallback":
            assert f.data == {"route": route}


def test_degeneracy_and_metric_findings():
    m = _metrics(x=0.0, y=0.0, w=0.5, h=0.5)
    codes = lambda o, mm=m: {f.code for f in check_outline(o, mm, NIB)}
    assert "outline_degenerate" in codes(Outline.empty())
    big = _outline([(0, 0), (500, 0), (500, 500), (0, 500)])
    assert codes(big) == set()
    tiny = Outline(contours=big.contours + _outline([(0, 0), (5, 0), (5, 5), (0, 5)]).contours, bounds=big.bounds)
    assert "outline_tiny_contour" in codes(tiny)
    many = Outline(contours=big.contours * 13, bounds=big.bounds)
    assert "outline_fragmented" in codes(many)
    assert "metrics_inconsistent" in codes(_outline([(0, 0), (600, 0), (600, 500), (0, 500)]))
    assert "metrics_suspicious" in codes(_outline([(0, 0), (2600, 0), (2600, 500), (0, 500)]), _metrics(w=2.6, h=0.5, advance=2.5))
    shifted = check_outline(big, _metrics(x=0.0, y=0.05, w=0.5, h=0.5, baseline=0.05), NIB)
    assert not any(f.code == "metrics_inconsistent" for f in shifted)   # bbox translated by -baseline too


def test_degenerate_stroke_yields_empty_outline_with_flags():
    res = outline_from_strokes(_strokes(_pts([(100, 100), (100, 100)])), _metrics(x=0.1, y=0.1, w=0.001, h=0.001, advance=0.5))
    assert res.outline.contours == () and res.outline.bounds is None and res.route == "empty"
    assert {f.code for f in res.findings} == {"stroke_degenerate", "outline_degenerate"}   # no outline_empty, no metrics_* on an empty outline
    assert {f.code for f in check_outline(Outline.empty(), _metrics(w=2.6, advance=2.5), NIB)} == {"outline_degenerate"}


def test_draw_round_trip_notdef_and_determinism():
    nd = notdef_outline()
    rec = RecordingPen()
    nd.draw(rec)
    assert rec.value[0] == ("moveTo", ((50, 0),)) and rec.value[-1] == ("closePath", ())
    assert nd.bounds == (50, 0, 450, 700) and len(nd.contours) == 2 and nd.contour_areas()[0] > 0 > nd.contour_areas()[1]
    s = _strokes(_pts(_circle()))
    a, b = outline_from_strokes(s, _metrics(x=0.1, y=0.1, w=0.4, h=0.4)), outline_from_strokes(s, _metrics(x=0.1, y=0.1, w=0.4, h=0.4))
    assert a.outline == b.outline


def test_fixed_point_route_elevates_the_quadratics_simplify_emits():
    from font_gen.synth import synth_sample
    sample, strokes, _ = synth_sample("B", "medi", 2, 1)   # qu2cu and elevation both fail their checks on this one
    res = outline_from_strokes(strokes, sample.metrics)
    assert res.route == "fixed_point"
    assert all(seg[0] in ("line", "curve") for c in res.outline.contours for seg in c.segments)

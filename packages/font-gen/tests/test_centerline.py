import math

import numpy as np
from fontTools.pens.recordingPen import RecordingPen

from font_gen.centerline import (RESAMPLE, catmull_rom_path, dedupe, dot_stub, is_closed_loop, is_dot, prepare,
                                 resample)

NIB = 45.0


def _circle(r=200.0, n=36, cx=300.0, cy=300.0, jitter=0.0, seed=1):
    rng = np.random.default_rng(seed)
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    pts = np.stack([cx + r * np.cos(t), cy + r * np.sin(t)], axis=1)
    return pts + rng.uniform(-jitter, jitter, size=pts.shape)


def test_dedupe_drops_points_closer_than_half_unit():
    out = dedupe(np.array([[0.0, 0.0], [0.2, 0.1], [5.0, 0.0], [5.0, 0.0]]))
    assert out.tolist() == [[0.0, 0.0], [5.0, 0.0]]


def test_dot_detection_and_stub():
    pts = np.array([[100.0, 100.0], [104.0, 100.0]])
    assert is_dot(pts, NIB)                        # length 4 < 22.5
    assert dot_stub(pts) == ((100.0, 100.0), (104.0, 100.0))
    diag = np.array([[0.0, 0.0], [3.0, 4.0]])
    (ax, ay), (bx, by) = dot_stub(diag)
    assert math.isclose(ax, 0.0, abs_tol=1e-9) and math.isclose(by, 4.0, abs_tol=1e-9)   # oriented first -> last
    assert dot_stub(np.array([[7.0, 7.0], [7.0, 7.0], [7.0, 7.0]])) == ((6.5, 7.0), (7.5, 7.0))   # zero covariance -> +x, length 1
    assert not is_dot(np.array([[0.0, 0.0], [30.0, 0.0]]), NIB)
    prepared = prepare(pts, NIB)
    assert prepared.kind == "dot" and prepared.stub is not None


def test_closed_loop_detected_and_closed():
    circ = _circle()
    assert is_closed_loop(circ, NIB)               # gap 34.9 < 67.5, length 1256 > 270
    assert prepare(circ, NIB).kind == "closed"
    arc = _circle()[:8]
    assert not is_closed_loop(arc, NIB) and prepare(arc, NIB).kind == "open"


def test_resample_spacing_8_plus_minus_half():
    line = np.array([[0.0, 0.0], [100.0, 0.0]])
    out = resample(line)
    gaps = np.linalg.norm(np.diff(out, axis=0), axis=1)
    assert len(out) == 14 and np.all(np.abs(gaps - RESAMPLE) <= 0.5)   # 100 / 8 = 12.5 rounds half-up to 13 gaps
    closed = resample(_circle(), closed=True)
    assert len(closed) == 157 and not np.allclose(closed[0], closed[-1])  # floor(1256.6 / 8 + 0.5)


def test_jittered_circle_smooths_to_within_quarter_nib():
    jittered = _circle(jitter=8.0)
    knots = prepare(jittered, NIB).knots
    radial = np.linalg.norm(knots - np.array([300.0, 300.0]), axis=1) - 200.0
    raw = np.linalg.norm(jittered - np.array([300.0, 300.0]), axis=1) - 200.0
    assert np.abs(radial).max() < 0.25 * NIB and np.abs(radial).max() < np.abs(raw).max()


def test_smoothing_off_keeps_knots_on_the_input_polyline():
    pts = np.array([[0.0, 0.0], [50.0, 40.0], [120.0, 10.0], [200.0, 90.0]])
    knots = prepare(pts, NIB, smooth_sigma=0.0).knots
    for k in knots:
        d = min(_dist_to_segment(k, pts[i], pts[i + 1]) for i in range(len(pts) - 1))
        assert d < 1e-6


def _dist_to_segment(p, a, b):
    ab = b - a
    t = np.clip(np.dot(p - a, ab) / np.dot(ab, ab), 0.0, 1.0)
    return float(np.linalg.norm(p - (a + t * ab)))


def test_catmull_rom_is_g1_at_interior_knots_and_closes_loops():
    knots = np.array([[0.0, 0.0], [50.0, 40.0], [120.0, 10.0], [200.0, 90.0]])
    rec = RecordingPen()
    catmull_rom_path(knots).draw(rec)
    curves = [args for op, args in rec.value if op == "curveTo"]
    assert len(curves) == 3 and rec.value[0] == ("moveTo", ((0.0, 0.0),))
    for i in range(1, 3):
        c1_prev_to_knot = np.array(curves[i - 1][2]) - np.array(curves[i - 1][1])  # p - c2 (incoming)
        knot_to_c1 = np.array(curves[i][0]) - np.array(curves[i - 1][2])          # c1 - p (outgoing)
        assert np.allclose(c1_prev_to_knot, knot_to_c1)
    rec2 = RecordingPen()
    catmull_rom_path(_circle(n=12), closed=True).draw(rec2)
    assert rec2.value[-1][0] == "closePath" and sum(op == "curveTo" for op, _ in rec2.value) == 12
    rec3 = RecordingPen()
    catmull_rom_path(knots, start=1, end=2).draw(rec3)
    assert rec3.value[0] == ("moveTo", ((50.0, 40.0),)) and sum(op == "curveTo" for op, _ in rec3.value) == 1
    assert rec3.value[1] == rec.value[2]   # the sub-range piece is the same cubic as in the whole path (no clamping at sub-range ends)


def test_degenerate_contour():
    assert prepare(np.array([[1.0, 1.0], [1.0, 1.0]]), NIB).kind == "degenerate"

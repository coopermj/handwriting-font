import numpy as np
import pytest
from hwfont_schema.strokes import Contour, StrokeData, StrokePoint

from font_gen.nib import (NIB, NIB_EM, clamp_slope, contour_widths, gaussian_smooth, nib_units, pressure_width,
                          sample_tier, split_runs)


def _c(points):
    return Contour(points=[StrokePoint(**p) for p in points])


def test_constants_and_pressure_model():
    assert NIB_EM == 0.045 and NIB == 45.0 and nib_units() == 45.0 and nib_units(0.05) == 50.0
    assert pressure_width(0.0, 45.0) == pytest.approx(31.5) and pressure_width(1.0, 45.0) == pytest.approx(58.5)
    assert pressure_width(0.5, 45.0) == pytest.approx(45.0)


def test_tier_priority_width_over_pressure_over_constant():
    both = StrokeData(contours=[_c([{"x": 0, "y": 0, "pressure": 0.5, "width": 0.05}, {"x": 1, "y": 1, "pressure": 0.5, "width": 0.05}])])
    assert sample_tier(both) == ("A", False)
    pressure = StrokeData(contours=[_c([{"x": 0, "y": 0, "pressure": 0.5}, {"x": 1, "y": 1}])])
    assert sample_tier(pressure) == ("B", True)
    plain = StrokeData(contours=[_c([{"x": 0, "y": 0}, {"x": 1, "y": 1}])])
    assert sample_tier(plain) == ("C", False)


def test_contour_widths_per_tier_with_clamps_and_gap_interpolation():
    a = _c([{"x": 0, "y": 0, "width": 0.01}, {"x": 1, "y": 0, "width": 0.05}, {"x": 2, "y": 0, "width": 0.15}])
    np.testing.assert_allclose(contour_widths(a, "A", 45.0), [18.0, 50.0, 90.0])          # clamp [0.4, 2.0] * NIB
    b = _c([{"x": 0, "y": 0, "pressure": 0.0}, {"x": 1, "y": 0}, {"x": 2, "y": 0, "pressure": 1.0}, {"x": 3, "y": 0}])
    np.testing.assert_allclose(contour_widths(b, "B", 45.0), [31.5, 45.0, 58.5, 58.5])    # gap interpolated, edge copied
    c = _c([{"x": 0, "y": 0}, {"x": 1, "y": 0}])
    np.testing.assert_allclose(contour_widths(c, "C", 45.0), [45.0, 45.0])


def test_gaussian_smooth_pins_open_endpoints_and_damps_noise():
    rng = np.random.default_rng(1)
    base = np.linspace(0.0, 100.0, 50)
    noisy = base + rng.uniform(-4, 4, size=50)
    out = gaussian_smooth(noisy, sigma_samples=1.5, closed=False)
    assert out[0] == noisy[0] and out[-1] == noisy[-1]
    assert np.abs(out[1:-1] - base[1:-1]).max() < np.abs(noisy[1:-1] - base[1:-1]).max()
    const = gaussian_smooth(np.full(20, 45.0), 1.5, closed=True)
    assert np.allclose(const, 45.0)
    assert gaussian_smooth(noisy, 0.0, closed=False).tolist() == noisy.tolist()
    xy = gaussian_smooth(np.stack([noisy, noisy], axis=1), 1.5, closed=False)
    assert xy.shape == (50, 2)


def test_slope_clamp_limits_change_per_unit_of_arc_length():
    out = clamp_slope(np.array([30.0, 60.0, 30.0]), step=8.0)
    assert np.abs(np.diff(out)).max() <= 0.7 * 8.0 + 1e-9
    assert out[0] == 30.0 and out[2] == 30.0


def test_split_runs_at_3_percent_with_shared_boundaries():
    assert split_runs([45, 45, 46, 50, 50, 46, 45]) == [(0, 3), (3, 5), (5, 6)]
    assert split_runs([45.0] * 10) == [(0, 9)]
    assert split_runs([45.0]) == []
    assert split_runs([30, 60]) == [(0, 1)]

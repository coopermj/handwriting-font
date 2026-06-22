import math

from hwfont_schema import Fiducial
from ingest_segment.align import apply_affine, estimate_affine, residual


def test_recovers_known_scale_and_translation():
    expected = [
        Fiducial(id="tl", x=40, y=40),
        Fiducial(id="tr", x=1360, y=40),
        Fiducial(id="bl", x=40, y=1830),
        Fiducial(id="br", x=1360, y=1830),
    ]
    # measured = expected scaled 0.5 and shifted (+5, -3)
    measured = {f.id: (f.x * 0.5 + 5.0, f.y * 0.5 - 3.0) for f in expected}

    m = estimate_affine(measured, expected)
    # applying the affine to measured points should recover expected
    for f in expected:
        ex, ey = apply_affine(m, [measured[f.id]])[0]
        assert abs(ex - f.x) < 1e-6 and abs(ey - f.y) < 1e-6
    assert residual(m, measured, expected) < 1e-6


def test_residual_reports_misfit():
    expected = [
        Fiducial(id="tl", x=0, y=0),
        Fiducial(id="tr", x=100, y=0),
        Fiducial(id="bl", x=0, y=100),
        Fiducial(id="br", x=100, y=100),
    ]
    measured = {f.id: (f.x, f.y) for f in expected}
    measured["br"] = (110.0, 90.0)  # perturb one mark
    m = estimate_affine(measured, expected)
    assert residual(m, measured, expected) > 1.0

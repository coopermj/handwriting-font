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


from hwfont_schema import Page
from ingest_segment.align import Alignment, align_page, apply_affine


def _page(fids):
    return Page(id="p0", width_px=200, height_px=200, dpi=226, fiducials=fids)


def test_align_page_uses_fiducials_when_available():
    fids = [
        Fiducial(id="tl", x=20, y=20),
        Fiducial(id="tr", x=180, y=20),
        Fiducial(id="bl", x=20, y=180),
        Fiducial(id="br", x=180, y=180),
    ]
    measured = {f.id: (f.x, f.y) for f in fids}  # identity export
    al = align_page(measured, _page(fids), export_size=(200, 200))
    assert isinstance(al, Alignment)
    assert al.method == "fiducial"
    assert al.low_confidence is False
    assert al.residual_px < 1e-6


def test_align_page_falls_back_to_geometric_scale():
    fids = [Fiducial(id="tl", x=20, y=20)]  # too few to estimate an affine
    # export is half-size; geometric scale should map export px -> page px (x2)
    al = align_page({}, _page(fids), export_size=(100, 100))
    assert al.method == "geometric_scale"
    sx, sy = apply_affine(al.matrix, [(50.0, 50.0)])[0]
    assert abs(sx - 100.0) < 1e-6 and abs(sy - 100.0) < 1e-6


def test_align_page_flags_low_confidence_on_high_residual():
    fids = [
        Fiducial(id="tl", x=0, y=0),
        Fiducial(id="tr", x=100, y=0),
        Fiducial(id="bl", x=0, y=100),
        Fiducial(id="br", x=100, y=100),
    ]
    measured = {f.id: (f.x, f.y) for f in fids}
    measured["br"] = (140.0, 60.0)  # large misfit
    al = align_page(measured, _page(fids), export_size=(100, 100), residual_threshold=2.0)
    assert al.method == "fiducial"
    assert al.low_confidence is True

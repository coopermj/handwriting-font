import io
import sys

from PIL import Image, ImageDraw
from hwfont_schema import BBox, Metrics

from font_gen.autotrace import raster_to_outline, skimage_available


def _ring_png(size=154, outer=64, width=12):
    img = Image.new("L", (size, size), 255)
    d = ImageDraw.Draw(img)
    c = size / 2
    d.ellipse([c - outer, c - outer, c + outer, c + outer], fill=0)
    d.ellipse([c - outer + width, c - outer + width, c + outer - width, c + outer - width], fill=255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


METRICS = Metrics(baseline=0.0, x_height=0.45, advance=0.6, bbox=BBox(x=0.05, y=0.0, w=0.5, h=0.5))  # padded: 0.6 em -> 154 px at 256


def test_synthetic_o_traces_to_two_georeferenced_contours():
    assert skimage_available()
    res = raster_to_outline(_ring_png(), METRICS, label="o", sample_id="r1")
    assert res.tier == "D" and len(res.outline.contours) == 2
    areas = sorted(res.outline.contour_areas(), key=abs)
    assert areas[-1] > 0 > areas[0]
    x0, y0, x1, y1 = res.outline.bounds
    assert -4 <= x0 and x1 <= 604 and -4 <= y0 and y1 <= 554          # inside the padded bbox within 1 px/S (~3.9 units)
    assert abs((x0 + x1) / 2 - 300) <= 8 and abs((y0 + y1) / 2 - 250) <= 8
    assert any(f.code == "raster_fallback" and f.severity == "warn" for f in res.findings)


def test_missing_scikit_image_yields_empty_outline_and_warning(monkeypatch):
    monkeypatch.setitem(sys.modules, "skimage", None)
    monkeypatch.setitem(sys.modules, "skimage.measure", None)
    monkeypatch.setitem(sys.modules, "skimage.filters", None)
    assert not skimage_available()
    res = raster_to_outline(_ring_png(), METRICS)
    assert res.outline.contours == () and [f.code for f in res.findings] == ["raster_unavailable"]


def test_baseline_translation_applies_to_raster_too():
    m = Metrics(baseline=0.05, x_height=0.45, advance=0.6, bbox=BBox(x=0.05, y=0.05, w=0.5, h=0.5))
    res = raster_to_outline(_ring_png(), m)
    assert abs((res.outline.bounds[1] + res.outline.bounds[3]) / 2 - 250) <= 8
    assert not any(f.code == "metrics_inconsistent" for f in res.findings)


def test_no_conic_to_in_autotrace():
    import inspect
    import font_gen.autotrace
    assert "conic" + "To" not in inspect.getsource(font_gen.autotrace)   # the same pin test_outline applies to centerline/outline

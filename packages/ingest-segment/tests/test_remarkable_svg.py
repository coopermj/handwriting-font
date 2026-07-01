import base64
import io

from PIL import Image

from ingest_segment.remarkable_svg import centerline, normalize, parse_svg


def test_skeletonize_dependency_importable():
    from skimage.morphology import skeletonize  # noqa: F401


def _png_b64(size=(20, 20), color=200):
    img = Image.new("L", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def _write_svg(tmp_path, body, viewbox="-50 0 100 80", with_image=True):
    img_el = (
        f'<image x="-50" y="0" width="100" height="80" '
        f'xlink:href="data:image/png;base64,{_png_b64()}"/>'
        if with_image
        else ""
    )
    svg = (
        f'<svg width="100" height="80" viewBox="{viewbox}" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink">{img_el}{body}</svg>'
    )
    p = tmp_path / "page.svg"
    p.write_text(svg, encoding="utf-8")
    return p


def test_parse_svg_extracts_viewbox_image_and_rings(tmp_path):
    body = (
        '<path fill="#000000" d="M-40 10 L-20 10 L-20 14 L-40 14 "/>'
        '<path fill="#000000" d="M0 30 L10 30 L10 34 L0 34 "/>'
    )
    svg = _write_svg(tmp_path, body)
    viewbox, template_png, rings = parse_svg(svg)

    assert viewbox == (-50.0, 0.0, 100.0, 80.0)
    assert template_png is not None
    assert Image.open(io.BytesIO(template_png)).size == (20, 20)
    assert len(rings) == 2
    # first ring parsed in viewBox coordinates (still centered/negative)
    assert rings[0][0] == (-40.0, 10.0)
    assert len(rings[0]) == 4


def test_parse_svg_without_image(tmp_path):
    svg = _write_svg(tmp_path, '<path fill="#000000" d="M0 0 L10 0 L10 5 L0 5 "/>', with_image=False)
    viewbox, template_png, rings = parse_svg(svg)
    assert template_png is None
    assert len(rings) == 1


def test_parse_svg_missing_viewbox_uses_width_height(tmp_path):
    svg = (
        '<svg width="120" height="90" xmlns="http://www.w3.org/2000/svg">'
        '<path fill="#000000" d="M1 1 L9 1 L9 5 L1 5 "/></svg>'
    )
    p = tmp_path / "p.svg"
    p.write_text(svg, encoding="utf-8")
    viewbox, _, rings = parse_svg(p)
    assert viewbox == (0.0, 0.0, 120.0, 90.0)
    assert len(rings) == 1


def test_normalize_shifts_by_viewbox_min():
    ring = [(-40.0, 10.0), (-20.0, 10.0)]
    out = normalize(ring, (-50.0, 0.0, 100.0, 80.0))
    assert out == [(10.0, 10.0), (30.0, 10.0)]  # +50 in x, +0 in y


def test_normalize_identity_when_origin_zero():
    ring = [(5.0, 5.0), (9.0, 12.0)]
    assert normalize(ring, (0.0, 0.0, 100.0, 100.0)) == ring


def test_centerline_of_filled_bar_is_horizontal_midline():
    # a 40-wide x 8-tall filled rectangle -> centerline ~ horizontal line at mid-height
    ring = [(10.0, 20.0), (50.0, 20.0), (50.0, 28.0), (10.0, 28.0)]
    line = centerline(ring)
    assert line is not None and len(line) >= 2
    xs = [p[0] for p in line]
    ys = [p[1] for p in line]
    assert min(xs) < 16 and max(xs) > 44          # spans the bar horizontally
    assert max(ys) - min(ys) <= 3                  # roughly flat, near mid-height (~24)
    assert 22 <= sum(ys) / len(ys) <= 26


def test_centerline_of_vertical_bar_is_vertical():
    ring = [(30.0, 10.0), (36.0, 10.0), (36.0, 60.0), (30.0, 60.0)]
    line = centerline(ring)
    ys = [p[1] for p in line]
    xs = [p[0] for p in line]
    assert min(ys) < 16 and max(ys) > 54
    assert max(xs) - min(xs) <= 3

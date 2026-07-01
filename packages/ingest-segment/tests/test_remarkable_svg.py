import base64
import io

from PIL import Image

from ingest_segment.remarkable_svg import parse_svg


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

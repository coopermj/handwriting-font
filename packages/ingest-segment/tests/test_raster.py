from PIL import Image, ImageDraw

from hwfont_schema import Fiducial
from ingest_segment.raster import detect_fiducials, load_raster


def _page_with_dots(tmp_path, marks, radius=10):
    img = Image.new("L", (200, 200), color=255)
    draw = ImageDraw.Draw(img)
    for m in marks:
        draw.ellipse([m.x - radius, m.y - radius, m.x + radius, m.y + radius], fill=0)
    path = tmp_path / "page.png"
    img.save(path)
    return path


def test_load_raster_returns_grayscale(tmp_path):
    path = _page_with_dots(tmp_path, [Fiducial(id="tl", x=20, y=20)])
    img = load_raster(path)
    assert img.mode == "L"
    assert img.size == (200, 200)


def test_detect_fiducials_recovers_known_centers(tmp_path):
    expected = [
        Fiducial(id="tl", x=20, y=20),
        Fiducial(id="tr", x=180, y=20),
        Fiducial(id="bl", x=20, y=180),
        Fiducial(id="br", x=180, y=180),
    ]
    path = _page_with_dots(tmp_path, expected)
    img = load_raster(path)

    found = detect_fiducials(img, expected, search_radius=30)
    assert set(found) == {"tl", "tr", "bl", "br"}
    for m in expected:
        fx, fy = found[m.id]
        assert abs(fx - m.x) <= 1.5 and abs(fy - m.y) <= 1.5


def test_detect_fiducials_skips_missing_marks(tmp_path):
    expected = [Fiducial(id="tl", x=20, y=20), Fiducial(id="tr", x=180, y=20)]
    # only draw tl
    path = _page_with_dots(tmp_path, [expected[0]])
    img = load_raster(path)
    found = detect_fiducials(img, expected, search_radius=30)
    assert set(found) == {"tl"}

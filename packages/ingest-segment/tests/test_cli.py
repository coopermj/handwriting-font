import base64 as _b64
import io as _io

import ingest_segment.run as run_mod
from hwfont_schema import BBox, CaptureSidecar, Fiducial, Page, Region
from ingest_segment.run import main
from ingest_segment.segment import VisionBox, VisionResult
from PIL import Image, ImageDraw


def _write_inputs(tmp_path):
    page = Page(
        id="p0", width_px=200, height_px=200, dpi=226,
        fiducials=[
            Fiducial(id="tl", x=20, y=20), Fiducial(id="tr", x=180, y=20),
            Fiducial(id="bl", x=20, y=180), Fiducial(id="br", x=180, y=180),
        ],
        regions=[Region(
            id="p0-r0", expected_transcript="hi", baseline_y=110.0,
            bbox=BBox(x=40.0, y=80.0, w=120.0, h=40.0), expected_units=["h", "i"],
        )],
    )
    sidecar = CaptureSidecar(pages=[page])
    (tmp_path / "capture.sidecar.json").write_text(sidecar.model_dump_json(), encoding="utf-8")

    img = Image.new("L", (200, 200), color=255)
    draw = ImageDraw.Draw(img)
    for x, y in [(20, 20), (180, 20), (20, 180), (180, 180)]:
        draw.ellipse([x - 8, y - 8, x + 8, y + 8], fill=0)
    img.save(tmp_path / "page0.png")


def test_cli_runs_with_mocked_vision(tmp_path, monkeypatch, capsys):
    _write_inputs(tmp_path)

    def fake_vision(crop_png, region):
        return VisionResult(boxes=[
            VisionBox(label="h", kind="single", x=0, y=0, w=50, h=40, confidence=0.9),
            VisionBox(label="i", kind="single", x=60, y=0, w=40, h=40, confidence=0.3),
        ])

    monkeypatch.setattr(run_mod, "_build_vision_client", lambda model: fake_vision)

    out = tmp_path / "out"
    rc = main([
        "--raster", str(tmp_path / "page0.png"),
        "--sidecar", str(tmp_path / "capture.sidecar.json"),
        "--out", str(out),
    ])
    assert rc == 0
    assert (out / "candidates.json").exists()
    printed = capsys.readouterr().out
    assert "2 candidate" in printed  # summary line
    # lowest-confidence-first ordering surfaced 'i' (0.3) before 'h' (0.9)
    assert printed.index("needs_review") < printed.index("pending")


def _remarkable_svg_and_sidecar(tmp_path):
    # template raster (page-px 200x200) with 4 corner fiducial dots
    tpl = Image.new("L", (200, 200), color=255)
    dt = ImageDraw.Draw(tpl)
    for x, y in [(20, 20), (180, 20), (20, 180), (180, 180)]:
        dt.ellipse([x - 8, y - 8, x + 8, y + 8], fill=0)
    buf = _io.BytesIO()
    tpl.save(buf, format="PNG")
    href = "data:image/png;base64," + _b64.standard_b64encode(buf.getvalue()).decode("ascii")

    # one filled-bar stroke inside the region, in centered viewBox coords (x offset -100)
    svg = (
        f'<svg width="200" height="200" viewBox="-100 0 200 200" '
        f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        f'<image x="-100" y="0" width="200" height="200" xlink:href="{href}"/>'
        f'<path fill="#000000" d="M-60 95 L-20 95 L-20 101 L-60 101 "/>'
        f'</svg>'
    )
    svg_path = tmp_path / "page1.svg"
    svg_path.write_text(svg, encoding="utf-8")

    page = Page(
        id="p0", width_px=200, height_px=200, dpi=226,
        fiducials=[
            Fiducial(id="tl", x=20, y=20), Fiducial(id="tr", x=180, y=20),
            Fiducial(id="bl", x=20, y=180), Fiducial(id="br", x=180, y=180),
        ],
        regions=[Region(
            id="p0-r0", expected_transcript="hi", baseline_y=110.0,
            bbox=BBox(x=40.0, y=80.0, w=120.0, h=40.0), expected_units=["h", "i"],
        )],
    )
    sc = tmp_path / "capture.sidecar.json"
    sc.write_text(CaptureSidecar(pages=[page]).model_dump_json(), encoding="utf-8")
    return svg_path, sc


def test_cli_ingests_remarkable_svg_without_raster(tmp_path, monkeypatch):
    svg_path, sc = _remarkable_svg_and_sidecar(tmp_path)

    def fake_vision(crop_png, region):
        return VisionResult(boxes=[
            VisionBox(label="h", kind="single", x=0, y=0, w=50, h=40, confidence=0.9),
            VisionBox(label="i", kind="single", x=60, y=0, w=40, h=40, confidence=0.4),
        ])

    monkeypatch.setattr(run_mod, "_build_vision_client", lambda model: fake_vision)

    out = tmp_path / "out"
    rc = main(["--svg", str(svg_path), "--sidecar", str(sc), "--out", str(out)])
    assert rc == 0
    assert (out / "candidates.json").exists()

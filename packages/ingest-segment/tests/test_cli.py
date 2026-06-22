from PIL import Image, ImageDraw

import ingest_segment.run as run_mod
from hwfont_schema import BBox, CaptureSidecar, Fiducial, Page, Region
from ingest_segment.run import main
from ingest_segment.segment import VisionBox, VisionResult


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

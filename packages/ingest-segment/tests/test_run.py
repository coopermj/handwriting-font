from PIL import Image, ImageDraw

from hwfont_schema import BBox, Fiducial, Page, Region
from ingest_segment.run import ingest_page
from ingest_segment.segment import VisionBox, VisionResult


def _page():
    return Page(
        id="p0", width_px=200, height_px=200, dpi=226,
        fiducials=[
            Fiducial(id="tl", x=20, y=20),
            Fiducial(id="tr", x=180, y=20),
            Fiducial(id="bl", x=20, y=180),
            Fiducial(id="br", x=180, y=180),
        ],
        regions=[
            Region(
                id="p0-r0", expected_transcript="hi", baseline_y=110.0,
                bbox=BBox(x=40.0, y=80.0, w=120.0, h=40.0),
                expected_units=["h", "i"],
            )
        ],
    )


def _raster_with_fiducials():
    img = Image.new("L", (200, 200), color=255)
    draw = ImageDraw.Draw(img)
    for x, y in [(20, 20), (180, 20), (20, 180), (180, 180)]:
        draw.ellipse([x - 8, y - 8, x + 8, y + 8], fill=0)
    return img


def _vision(crop_png, region):
    return VisionResult(
        boxes=[
            VisionBox(label="h", kind="single", x=0, y=0, w=50, h=40, confidence=0.9),
            VisionBox(label="i", kind="single", x=60, y=0, w=40, h=40, confidence=0.85),
        ]
    )


def test_ingest_page_emits_candidate_set(tmp_path):
    out = tmp_path / "out"
    cs = ingest_page(
        page=_page(),
        raster=_raster_with_fiducials(),
        export_size=(200, 200),
        page_strokes_export=[[(45.0, 95.0), (45.0, 110.0)]],  # inside region/box 'h'
        vision=_vision,
        model="claude-opus-4-8",
        created_at="2026-06-22T00:00:00Z",
        out_dir=out,
    )
    assert cs.provenance.alignment_method == "fiducial"
    assert {c.label for c in cs.candidates} == {"h", "i"}
    # lowest confidence first
    assert cs.candidates[0].confidence <= cs.candidates[-1].confidence
    # the 'h' candidate got the stroke (identity export -> page px unchanged)
    h = next(c for c in cs.candidates if c.label == "h")
    assert h.strokes_path is not None
    assert (out / "candidates.json").exists()

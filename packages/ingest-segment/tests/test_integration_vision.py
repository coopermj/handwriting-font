import os

import pytest
from PIL import Image, ImageDraw

from hwfont_schema import BBox, Region
from ingest_segment.segment import ClaudeVisionClient

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; skipping real-API integration test",
)


def _crop_png_with_text():
    img = Image.new("L", (240, 80), color=255)
    draw = ImageDraw.Draw(img)
    draw.text((10, 25), "cat", fill=0)
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_real_vision_returns_boxes():
    import anthropic

    client = ClaudeVisionClient(anthropic.Anthropic())
    region = Region(
        id="p0-r0", expected_transcript="cat", baseline_y=60.0,
        bbox=BBox(x=0, y=0, w=240, h=80), expected_units=["c", "a", "t"],
    )
    result = client(_crop_png_with_text(), region)
    assert len(result.boxes) >= 1
    for box in result.boxes:
        assert 0.0 <= box.confidence <= 1.0
        assert box.w > 0 and box.h > 0

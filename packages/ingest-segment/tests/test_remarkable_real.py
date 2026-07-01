import os

import pytest

from ingest_segment.remarkable_svg import load_remarkable_export

_EXPORT = os.environ.get("HWF_REMARKABLE_SVG")

pytestmark = pytest.mark.skipif(
    not (_EXPORT and os.path.exists(_EXPORT)),
    reason="set HWF_REMARKABLE_SVG to a real reMarkable export path to run this test",
)


def test_real_export_yields_strokes_and_raster():
    exp = load_remarkable_export(_EXPORT)
    assert len(exp.strokes) > 0
    assert exp.page_raster.size[0] > 0 and exp.page_raster.size[1] > 0
    # centerlines are within the page bounds
    w, h = exp.page_size
    for stroke in exp.strokes:
        for x, y in stroke:
            assert -1 <= x <= w + 1 and -1 <= y <= h + 1

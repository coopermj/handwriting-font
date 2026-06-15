import pytest
from pydantic import ValidationError

from hwfont_schema.geometry import BBox


def test_bbox_accepts_valid_values():
    box = BBox(x=1.0, y=2.0, w=3.0, h=4.0)
    assert (box.x, box.y, box.w, box.h) == (1.0, 2.0, 3.0, 4.0)


def test_bbox_rejects_nonpositive_width():
    with pytest.raises(ValidationError):
        BBox(x=0, y=0, w=0, h=5)


def test_bbox_rejects_nonpositive_height():
    with pytest.raises(ValidationError):
        BBox(x=0, y=0, w=5, h=-1)

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from hwfont_schema import Fiducial

# a fiducial search window must contain at least this many dark pixels to count
_MIN_DARK_PIXELS = 8
# 0..255 grayscale; below this is "dark ink/mark"
_DARK_LEVEL = 96


def load_raster(path: str | Path) -> Image.Image:
    """Load a page raster as an 8-bit grayscale Pillow image."""
    return Image.open(path).convert("L")


def detect_fiducials(
    image: Image.Image,
    expected: list[Fiducial],
    search_radius: int = 60,
    dark_level: int = _DARK_LEVEL,
) -> dict[str, tuple[float, float]]:
    """Find each expected mark's dark-pixel centroid within a window around its known position.

    Returns {fiducial_id: (x, y)} for every mark with enough dark pixels; marks not
    found (too few dark pixels) are omitted so the caller can fall back.
    """
    arr = np.asarray(image, dtype=np.uint8)
    h, w = arr.shape
    found: dict[str, tuple[float, float]] = {}
    for mark in expected:
        x0 = max(0, int(mark.x - search_radius))
        x1 = min(w, int(mark.x + search_radius))
        y0 = max(0, int(mark.y - search_radius))
        y1 = min(h, int(mark.y + search_radius))
        window = arr[y0:y1, x0:x1]
        ys, xs = np.where(window < dark_level)
        if xs.size < _MIN_DARK_PIXELS:
            continue
        found[mark.id] = (float(xs.mean()) + x0, float(ys.mean()) + y0)
    return found

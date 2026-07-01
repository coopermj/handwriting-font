from __future__ import annotations

import base64
import re
import xml.etree.ElementTree as ET
from pathlib import Path

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_NUM = re.compile(r"-?\d+(?:\.\d+)?")

# a viewBox tuple is (min_x, min_y, width, height)
ViewBox = tuple[float, float, float, float]


def _local(tag: str) -> str:
    """Strip an XML namespace, e.g. '{http://...}path' -> 'path'."""
    return tag.rsplit("}", 1)[-1]


def _parse_path_d(d: str) -> list[tuple[float, float]]:
    """Parse an M/L path 'd' string into an ordered ring of (x, y) points.

    The reMarkable export uses only absolute M/L line segments, so we take the
    coordinate numbers in order and pair them.
    """
    nums = [float(n) for n in _NUM.findall(d)]
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


def parse_svg(svg_path: str | Path) -> tuple[ViewBox, bytes | None, list[list[tuple[float, float]]]]:
    """Parse a reMarkable SVG export into (viewBox, template PNG bytes | None, fill rings).

    Rings are one per `<path>`, in the SVG's own (possibly centered) coordinates.
    """
    root = ET.parse(str(svg_path)).getroot()

    vb = root.get("viewBox")
    if vb:
        minx, miny, w, h = (float(v) for v in vb.split())
    else:
        minx, miny = 0.0, 0.0
        w, h = float(root.get("width", "0")), float(root.get("height", "0"))

    template: bytes | None = None
    rings: list[list[tuple[float, float]]] = []
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "image" and template is None:
            href = el.get(_XLINK_HREF) or el.get("href") or ""
            if href.startswith("data:image") and "," in href:
                template = base64.b64decode(href.split(",", 1)[1])
        elif tag == "path":
            ring = _parse_path_d(el.get("d", ""))
            if len(ring) >= 2:
                rings.append(ring)

    return (minx, miny, w, h), template, rings

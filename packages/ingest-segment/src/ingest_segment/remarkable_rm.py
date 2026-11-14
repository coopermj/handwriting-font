"""reMarkable native `.rm` (v6 "lines") adapter.

The device's own stroke files carry what the SVG export loses: ordered pen points with
pressure, speed, tilt direction and brush width, in writing order. No skeletonization
is needed; each stroke is one centerline with per-point width and pressure.

The `.rm` file holds no page template, so the page raster is ink-on-white unless a
template PNG (the rendered capture page) is supplied, which also makes the printed
fiducials detectable for alignment.

Coordinates: v6 points are centered on x=0 (the page's horizontal middle) with y
running down from the top, in device pixels. They are shifted by +page_w/2 so they
land in the same top-left page-pixel convention as the SVG adapter; the fiducial
affine then reconciles device pixels with the sidecar's page pixels.

Requires the optional `rmscene` dependency (`pip install ingest-segment[rm]`).
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from ingest_segment.remarkable_svg import PenStroke, Point, RemarkableExport, compose_raster

_MAX_PRESSURE = 255.0  # v6 Point.pressure is 0..255


def _require_rmscene():
    try:
        import rmscene  # noqa: F401
        from rmscene import SceneLineItemBlock, read_blocks
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "reading native .rm files needs the 'rmscene' package: pip install 'ingest-segment[rm]'"
        ) from e
    return read_blocks, SceneLineItemBlock


def iter_lines(rm_path: str | Path) -> Iterable[Any]:
    """Yield every `rmscene.scene_items.Line` in a v6 .rm file, in file (writing) order."""
    read_blocks, SceneLineItemBlock = _require_rmscene()
    with open(rm_path, "rb") as f:
        for block in read_blocks(f):
            if isinstance(block, SceneLineItemBlock) and block.item.value is not None:
                yield block.item.value


def strokes_from_lines(lines: Iterable[Any], offset: tuple[float, float]) -> list[PenStroke]:
    """Convert rmscene Line objects (anything with `.points` of x/y/width/pressure) to PenStrokes."""
    dx, dy = offset
    strokes: list[PenStroke] = []
    for line in lines:
        pts: Sequence[Any] = getattr(line, "points", ())
        if len(pts) < 2:
            continue
        centerline: list[Point] = [(float(p.x) + dx, float(p.y) + dy) for p in pts]
        widths = [max(1.0, float(p.width)) for p in pts]
        pressures = [min(1.0, max(0.0, float(p.pressure) / _MAX_PRESSURE)) for p in pts]
        strokes.append(
            PenStroke(
                centerlines=[centerline],
                widths=[widths],
                outline=[],
                coverage=1.0,
                pressures=[pressures],
            )
        )
    return strokes


def load_rm_export(
    rm_path: str | Path,
    page_size: tuple[int, int],
    template: bytes | Image.Image | None = None,
) -> RemarkableExport:
    """Ingest a native .rm page into a page raster + pen strokes (page-px, top-left origin).

    `page_size` is the device page size in pixels (the sidecar's page size when the
    capture PDF fills the screen). Raises ValueError if the file holds no strokes.
    """
    w, h = page_size
    offset = (w / 2.0, 0.0)
    strokes = strokes_from_lines(iter_lines(rm_path), offset)
    if not strokes:
        raise ValueError(f"no pen strokes found in {rm_path}")

    tpl: Image.Image | None = None
    if template is not None:
        tpl = template if isinstance(template, Image.Image) else Image.open(io.BytesIO(template))
        tpl = tpl.convert("L")

    raster = compose_raster(tpl, strokes, (w, h))
    return RemarkableExport(
        page_raster=raster,
        strokes=strokes,
        page_size=(w, h),
        viewbox_offset=(-offset[0], -offset[1]),
        dropped_paths=0,
        has_template=tpl is not None,
        template_size=tpl.size if tpl is not None else None,
    )

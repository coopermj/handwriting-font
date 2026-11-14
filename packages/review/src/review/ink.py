"""Ink geometry: extents, the per-region stroke pool, ingest-faithful stroke
assignment, em-v1 raster resampling and page compositing.

Page pixels are y-down. Stroke points are used exactly as stored (Review Focus 1):
the extent of a stroke-bearing candidate is the bbox of its points, never its box.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from hwfont_schema import BBox, Candidate, Contour, StrokeData, StrokePoint
from hwfont_schema.emspace import RASTER_PAD
from PIL import Image

from review.inputs import PageInputs

Polyline = list[tuple[float, float]]

INK_THRESHOLD = 128  # L value below which a pixel is ink
INSIDE_FRACTION = 0.5  # ingest's stroke-inside rule (inclusive box edges)
RASTER_SCALE = 256  # em-v1 recommended px/em for sample rasters
WHITE = 255


@dataclass(frozen=True)
class Extent:
    xmin: float
    xmax: float
    ymin: float
    ymax: float


def crop_origin(bbox: BBox) -> tuple[int, int]:
    """Page-px top-left of an ingest crop: ingest truncates the box origin with int()."""
    return int(bbox.x), int(bbox.y)


def strokes_to_polylines(strokes: StrokeData) -> list[Polyline]:
    return [[(float(p.x), float(p.y)) for p in c.points] for c in strokes.contours]


def stroke_extent(polylines: Sequence[Polyline]) -> Extent | None:
    xs = [x for pl in polylines for x, _ in pl]
    ys = [y for pl in polylines for _, y in pl]
    if not xs:
        return None
    return Extent(xmin=min(xs), xmax=max(xs), ymin=min(ys), ymax=max(ys))


def ink_mask(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L")) < INK_THRESHOLD


def crop_extent(crop_png: bytes, origin: tuple[int, int]) -> Extent | None:
    """Ink-pixel extent of a crop in page px; max edges are exclusive (col_max + 1)."""
    with Image.open(io.BytesIO(crop_png)) as im:
        mask = ink_mask(im)
    rows, cols = np.nonzero(mask)
    if cols.size == 0:
        return None
    ox, oy = origin
    return Extent(
        xmin=ox + int(cols.min()),
        xmax=ox + int(cols.max()) + 1,
        ymin=oy + int(rows.min()),
        ymax=oy + int(rows.max()) + 1,
    )


def candidate_extent(candidate: Candidate, strokes: StrokeData | None, crop: bytes | None) -> Extent | None:
    """Stroke-point extent when strokes exist, else the crop's ink-pixel extent."""
    if strokes is not None:
        ext = stroke_extent(strokes_to_polylines(strokes))
        if ext is not None:
            return ext
    if crop is not None:
        return crop_extent(crop, crop_origin(candidate.bbox))
    return None


def region_pool(page: PageInputs, region_id: str) -> list[Polyline]:
    """All distinct polylines of a region's candidates, first occurrence in manifest order."""
    seen: set[tuple[tuple[float, float], ...]] = set()
    pool: list[Polyline] = []
    for cid in page.candidate_ids(region_id):
        sd = page.strokes.get(cid)
        if sd is None:
            continue
        for pl in strokes_to_polylines(sd):
            key = tuple(pl)
            if key in seen:
                continue
            seen.add(key)
            pool.append(pl)
    return pool


def _inside_fraction(points: Polyline, box: BBox) -> float:
    if not points:
        return 0.0
    inside = sum(1 for x, y in points if box.x <= x <= box.x + box.w and box.y <= y <= box.y + box.h)
    return inside / len(points)


def assign_strokes(pool: Sequence[Polyline], bbox: BBox) -> list[Polyline]:
    """Polylines with ≥ INSIDE_FRACTION of their points inside `bbox`, in pool order."""
    return [list(pl) for pl in pool if pl and _inside_fraction(list(pl), bbox) >= INSIDE_FRACTION]


def polylines_to_strokes(polylines: Sequence[Polyline], template: StrokeData | None = None) -> StrokeData | None:
    """Contours from polylines (< 2 points dropped); pressure/width reused from a template
    contour with an identical xy sequence. None when nothing remains."""
    by_xy: dict[tuple[tuple[float, float], ...], Contour] = {}
    if template is not None:
        for c in template.contours:
            by_xy.setdefault(tuple((float(p.x), float(p.y)) for p in c.points), c)
    contours: list[Contour] = []
    for pl in polylines:
        if len(pl) < 2:
            continue
        key = tuple((float(x), float(y)) for x, y in pl)
        tc = by_xy.get(key)
        if tc is not None:
            pts = [StrokePoint(x=x, y=y, pressure=p.pressure, width=p.width) for (x, y), p in zip(key, tc.points)]
        else:
            pts = [StrokePoint(x=x, y=y) for x, y in key]
        contours.append(Contour(points=pts))
    if not contours:
        return None
    return StrokeData(contours=contours)


def resample_raster(
    source: Image.Image,
    source_origin: tuple[int, int],
    bbox_em: BBox,
    origin_x_px: float,
    baseline_px: float,
    s: float,
) -> bytes:
    """em-v1 sample raster (PNG, mode L) at RASTER_SCALE px/em covering bbox_em ± RASTER_PAD.

    `source` is a page-px image whose pixel (0, 0) is page px `source_origin`;
    `s` is em per page px. Area outside the source is white.
    """
    size = (
        round((bbox_em.w + 2 * RASTER_PAD) * RASTER_SCALE),
        round((bbox_em.h + 2 * RASTER_PAD) * RASTER_SCALE),
    )
    ox, oy = source_origin
    step = 1.0 / (s * RASTER_SCALE)  # source px per output px
    a, b, c = step, 0.0, origin_x_px + (bbox_em.x - RASTER_PAD) / s - ox
    d, e, f = 0.0, step, baseline_px - (bbox_em.y + bbox_em.h + RASTER_PAD) / s - oy
    out = source.convert("L").transform(
        size,
        Image.Transform.AFFINE,
        (a, b, c, d, e, f),
        resample=Image.Resampling.BICUBIC,
        fillcolor=WHITE,
    )
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def composite_page(page: PageInputs) -> Image.Image:
    """The page raster, or (without one) every crop pasted on white at its crop origin."""
    if page.raster is not None:
        return page.raster
    canvas = Image.new("L", (page.page.width_px, page.page.height_px), WHITE)
    for cand in page.candidate_set.candidates:
        data = page.crops.get(cand.id)
        if data is None:
            continue
        with Image.open(io.BytesIO(data)) as im:
            canvas.paste(im.convert("L"), crop_origin(cand.bbox))
    return canvas

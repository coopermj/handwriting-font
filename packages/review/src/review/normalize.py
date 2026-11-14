"""em-v1 materialisation: an accepted candidate to a Contract Y Sample + sidecars.

Frame, unit, origin and half-gap rule exactly as `hwfont_schema.emspace`. Neighbour
geometry comes from the ORIGINAL candidates only (spec ruling 3), so a review
operation changes exactly one sample. Nothing is smoothed, resampled or rotated.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from hwfont_schema import BBox, Contour, Metrics, Quality, ReviewStatus, Sample, StrokeData, StrokePoint
from hwfont_schema.emspace import check_sample_em
from PIL import Image
from pydantic import ValidationError

from review.decisions import ReviewLog
from review.errors import ReviewError
from review.ink import (
    Extent,
    assign_strokes,
    candidate_extent,
    crop_origin,
    ink_mask,
    polylines_to_strokes,
    region_pool,
    resample_raster,
    stroke_extent,
    strokes_to_polylines,
)
from review.inputs import PageInputs, ReviewInputs
from review.session import SessionStats, context_mismatch, neighbours, scale

ADVANCE_CLAMP = 0.02


@dataclass(frozen=True)
class Materialised:
    sample: Sample
    strokes: StrokeData | None
    raster: bytes | None
    flags: tuple[str, ...]


def sample_id(session_id: str, candidate_id: str) -> str:
    return f"{session_id}-{candidate_id}"


def _mask_extent(mask: np.ndarray, origin: tuple[int, int], box: BBox) -> Extent | None:
    """Ink-pixel extent (page px, exclusive maxima) of `mask` at `origin`, restricted to `box`."""
    ox, oy = origin
    x0, y0 = int(box.x) - ox, int(box.y) - oy
    x1, y1 = int(box.x + box.w) - ox, int(box.y + box.h) - oy
    h, w = mask.shape
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
    if x1 <= x0 or y1 <= y0:
        return None
    rows, cols = np.nonzero(mask[y0:y1, x0:x1])
    if cols.size == 0:
        return None
    return Extent(
        xmin=ox + x0 + int(cols.min()),
        xmax=ox + x0 + int(cols.max()) + 1,
        ymin=oy + y0 + int(rows.min()),
        ymax=oy + y0 + int(rows.max()) + 1,
    )


def _decode(png: bytes) -> Image.Image:
    with Image.open(io.BytesIO(png)) as im:
        return im.convert("L")


def _raster_ink(page: PageInputs, candidate_id: str, box: BBox) -> Extent | None:
    """Ink pixels inside the decision box: page raster if present, else the crop."""
    if page.raster is not None:
        return _mask_extent(ink_mask(page.raster), (0, 0), box)
    crop = page.crops.get(candidate_id)
    if crop is None:
        return None
    return _mask_extent(ink_mask(_decode(crop)), crop_origin(page.candidates[candidate_id].bbox), box)


def _neighbour_extent(page: PageInputs, nid: str | None) -> Extent | None:
    if nid is None:
        return None
    return candidate_extent(page.candidates[nid], page.strokes.get(nid), page.crops.get(nid))


def _dedupe(items: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def materialise(inputs: ReviewInputs, stats: SessionStats, log: ReviewLog, candidate_id: str) -> Materialised:
    page = inputs.page_of(candidate_id)
    candidate = page.candidates[candidate_id]
    decision = log.decisions.get(candidate_id)
    if decision is None:
        raise ReviewError(f"no decision for candidate {candidate_id!r}")
    region = page.region(candidate.region_id)
    s = scale(region)
    baseline_px = region.baseline_y
    flags: list[str] = []
    quality = decision.quality

    # 2. strokes
    if decision.bbox == candidate.bbox:
        src = page.strokes.get(candidate_id)
    else:
        src = polylines_to_strokes(
            assign_strokes(region_pool(page, candidate.region_id), decision.bbox),
            template=page.strokes.get(candidate_id),
        )
    # 3. ink extent
    ink = stroke_extent(strokes_to_polylines(src)) if src is not None else None
    if ink is None:
        src = None
        ink = _raster_ink(page, candidate_id, decision.bbox)
        if ink is None:
            raise ReviewError(f"no ink for candidate {candidate_id!r}")
        flags.append("raster_only")

    # 4. half-gaps from the original neighbours
    left_id, right_id = neighbours(page, candidate.region_id, candidate_id)
    el, er = _neighbour_extent(page, left_id), _neighbour_extent(page, right_id)
    median_px = stats.median_half_gap / s
    lsb_px = 0.5 * (ink.xmin - el.xmax) if el is not None else median_px
    rsb_px = 0.5 * (er.xmin - ink.xmax) if er is not None else median_px
    if any(context_mismatch(page, candidate.region_id, candidate_id)):
        flags.append("neighbor_mismatch")

    # 5–6. transform and tight box
    origin_x_px = ink.xmin - lsb_px
    strokes_em: StrokeData | None = None
    if src is not None:
        strokes_em = StrokeData(
            contours=[
                Contour(
                    points=[
                        StrokePoint(
                            x=(p.x - origin_x_px) * s,
                            y=(baseline_px - p.y) * s,
                            pressure=p.pressure,
                            width=p.width * s if p.width is not None else None,
                        )
                        for p in c.points
                    ]
                )
                for c in src.contours
            ]
        )
        pts = [p for c in strokes_em.contours for p in c.points]
        minx, maxx = min(p.x for p in pts), max(p.x for p in pts)
        miny, maxy = min(p.y for p in pts), max(p.y for p in pts)
    else:
        minx, maxx = (ink.xmin - origin_x_px) * s, (ink.xmax - origin_x_px) * s
        miny, maxy = (baseline_px - ink.ymax) * s, (baseline_px - ink.ymin) * s
    if maxx - minx <= 0 or maxy - miny <= 0:
        raise ReviewError(f"degenerate ink for candidate {candidate_id!r}: zero-width or zero-height extent")
    bbox_em = BBox(x=minx, y=miny, w=maxx - minx, h=maxy - miny)

    # 7. advance
    advance = bbox_em.x + bbox_em.w + rsb_px * s
    if advance <= 0:
        advance = ADVANCE_CLAMP
        flags.append("advance_clamped")
        if quality == Quality.good:
            quality = Quality.marginal

    # 9. raster
    raster: bytes | None = None
    if page.raster is not None:
        raster = resample_raster(page.raster, (0, 0), bbox_em, origin_x_px, baseline_px, s)
    elif src is None:
        crop = page.crops[candidate_id]  # present: raster-only ink came from it
        raster = resample_raster(_decode(crop), crop_origin(candidate.bbox), bbox_em, origin_x_px, baseline_px, s)

    # 8. sample (schema failure → ReviewError, nothing written)
    try:
        sample = Sample(
            id=sample_id(log.session_id, candidate_id),
            label=decision.label,
            kind=decision.kind,
            context=candidate.context,
            metrics=Metrics(baseline=0.0, x_height=stats.x_height, advance=advance, bbox=bbox_em),
            quality=quality,
            review_status=ReviewStatus.accepted,
            capture_session_id=log.session_id,
            created_at=decision.updated_at,
        )
    except ValidationError as exc:
        raise ReviewError(f"sample for candidate {candidate_id!r} fails the schema: {exc}") from exc

    # 10. em-v1 check
    violations = check_sample_em(sample, strokes_em, raster)
    if violations:
        flags.extend(v.split(": ", 1)[0] for v in violations)
        if sample.quality == Quality.good:
            sample = sample.model_copy(update={"quality": Quality.marginal})
    return Materialised(sample=sample, strokes=strokes_em, raster=raster, flags=_dedupe(flags))

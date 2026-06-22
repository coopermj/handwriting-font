from __future__ import annotations

from pathlib import Path

from PIL import Image

from hwfont_schema import CandidateProvenance, CandidateSet, Page
from ingest_segment.align import apply_affine, align_page
from ingest_segment.candidates_out import write_candidate_set
from ingest_segment.raster import detect_fiducials
from ingest_segment.segment import VisionFn, _crop_png, segment_region


def ingest_page(
    page: Page,
    raster: Image.Image,
    export_size: tuple[int, int],
    page_strokes_export: list[list[tuple[float, float]]],
    vision: VisionFn,
    model: str,
    created_at: str,
    out_dir: str | Path,
    source_raster: str = "page.png",
    source_svg: str | None = None,
    force: bool = False,
) -> CandidateSet:
    """Run the full pipeline for one page; emit a CandidateSet directory.

    `page_strokes_export` are ink strokes in the SVG/export coordinate space; they
    are transformed into page-pixel space by the chosen alignment.
    """
    measured = detect_fiducials(raster, page.fiducials)
    alignment = align_page(measured, page, export_size)
    page_strokes = [apply_affine(alignment.matrix, s) for s in page_strokes_export]

    items: list = []
    crops: dict[str, bytes] = {}
    for region in page.regions:
        region_items = segment_region(
            region=region,
            raster=raster,
            page_strokes=page_strokes,
            vision=vision,
            page_id=page.id,
            alignment_method=alignment.method,
            page_low_confidence=alignment.low_confidence,
            model=model,
            created_at=created_at,
        )
        for candidate, strokes in region_items:
            crops[candidate.id] = _crop_png(raster, candidate.bbox)
            items.append((candidate, strokes))

    provenance = CandidateProvenance(
        source_page_id=page.id,
        source_raster=source_raster,
        source_svg=source_svg,
        alignment_method=alignment.method,
        alignment_residual_px=alignment.residual_px,
        model=model,
    )
    return write_candidate_set(out_dir, provenance, items, crops=crops, force=force)

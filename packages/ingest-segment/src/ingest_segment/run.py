from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from hwfont_schema import CandidateProvenance, CandidateSet, CaptureSidecar, Page
from ingest_segment.align import apply_affine, align_page
from ingest_segment.candidates_out import write_candidate_set
from ingest_segment.raster import detect_fiducials, load_raster
from ingest_segment.segment import (
    VISION_MODEL,
    ClaudeVisionClient,
    VisionFn,
    _crop_png,
    segment_region,
)
from ingest_segment.svg_strokes import parse_svg_strokes, separate_ink


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


def _build_vision_client(model: str) -> VisionFn:
    """Construct the real Claude vision client (reads ANTHROPIC_API_KEY via the SDK)."""
    import anthropic

    return ClaudeVisionClient(anthropic.Anthropic(), model=model)


def _ink_strokes_from_svg(svg_path: str) -> list[list[tuple[float, float]]]:
    ink = separate_ink(parse_svg_strokes(svg_path))
    return [s.points for s in ink]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Segment a written-on capture page into candidate glyph samples."
    )
    parser.add_argument("--raster", required=True, help="page raster PNG (required)")
    parser.add_argument("--sidecar", required=True, help="capture.sidecar.json (Contract X)")
    parser.add_argument("--svg", default=None, help="optional SVG ink export")
    parser.add_argument("--out", required=True, help="output CandidateSet directory")
    parser.add_argument("--page-index", type=int, default=0, help="page in the sidecar (default: 0)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing output dir")
    parser.add_argument("--model", default=VISION_MODEL, help=f"vision model (default: {VISION_MODEL})")
    parser.add_argument("--created-at", default=None, help="ISO timestamp stamped on candidates")
    args = parser.parse_args(argv)

    sidecar = CaptureSidecar.model_validate_json(Path(args.sidecar).read_text(encoding="utf-8"))
    if args.page_index < 0 or args.page_index >= len(sidecar.pages):
        print(f"page index {args.page_index} out of range (sidecar has {len(sidecar.pages)} pages)")
        return 1
    page = sidecar.pages[args.page_index]

    raster = load_raster(args.raster)
    if raster.size != (page.width_px, page.height_px):
        print(
            f"raster size {raster.size} != sidecar page size "
            f"({page.width_px}, {page.height_px})"
        )
        return 1

    if args.svg:
        strokes_export = _ink_strokes_from_svg(args.svg)
        export_size = raster.size  # SVG ink already in raster-pixel space when paired with this raster
    else:
        strokes_export = []
        export_size = raster.size

    created_at = args.created_at or "1970-01-01T00:00:00Z"
    cs = ingest_page(
        page=page,
        raster=raster,
        export_size=export_size,
        page_strokes_export=strokes_export,
        vision=_build_vision_client(args.model),
        model=args.model,
        created_at=created_at,
        out_dir=args.out,
        source_raster=Path(args.raster).name,
        source_svg=Path(args.svg).name if args.svg else None,
        force=args.force,
    )

    print(
        f"Wrote {len(cs.candidates)} candidate(s) to {args.out} "
        f"(alignment: {cs.provenance.alignment_method})."
    )
    for c in cs.candidates:  # already sorted lowest-confidence-first
        print(f"  {c.confidence:.2f}  {c.status.value:13s}  {c.label!r} ({c.kind.value})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

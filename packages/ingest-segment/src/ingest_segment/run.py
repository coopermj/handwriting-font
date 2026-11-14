from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from hwfont_schema import Candidate, CandidateProvenance, CandidateSet, CaptureSidecar, Page
from ingest_segment.align import Alignment, align_page, transform_stroke, warp_raster
from ingest_segment.candidates_out import write_candidate_set
from ingest_segment.debug import render_overlay
from ingest_segment.raster import detect_fiducials, load_raster
from ingest_segment.remarkable_svg import PenStroke, RemarkableExport, load_remarkable_export
from ingest_segment.segment import (
    VISION_MODEL,
    ClaudeVisionClient,
    VisionFn,
    _crop_png,
    segment_region,
)


@dataclass
class PreparedPage:
    """A page brought into Contract X page-pixel space: raster, strokes, and how."""

    raster: Image.Image
    strokes: list[PenStroke]
    alignment: Alignment
    measured: dict[str, tuple[float, float]]


def prepare_page(page: Page, raster: Image.Image, strokes: list[PenStroke]) -> PreparedPage:
    """Detect fiducials, choose the export->page affine, and apply it to raster AND strokes.

    `raster` and `strokes` must share one coordinate space (the export's). Both come out
    in sidecar page-pixel space, so region bboxes index the raster directly.
    """
    measured = detect_fiducials(raster, page.fiducials)
    alignment = align_page(measured, page, raster.size)
    page_size = (page.width_px, page.height_px)
    return PreparedPage(
        raster=warp_raster(raster, alignment.matrix, page_size),
        strokes=[transform_stroke(alignment.matrix, s) for s in strokes],
        alignment=alignment,
        measured=measured,
    )


def ingest_page(
    page: Page,
    raster: Image.Image,
    strokes: list[PenStroke],
    vision: VisionFn,
    model: str,
    created_at: str,
    out_dir: str | Path,
    source_raster: str | None = None,
    source_svg: str | None = None,
    source_rm: str | None = None,
    force: bool = False,
    debug_dir: str | Path | None = None,
) -> CandidateSet:
    """Run the full pipeline for one page; emit a CandidateSet directory.

    `raster` and `strokes` are in the export's coordinate space; the chosen alignment
    brings both into page-pixel space before segmentation.
    """
    prepared = prepare_page(page, raster, strokes)
    alignment = prepared.alignment

    items: list[tuple[Candidate, list[PenStroke]]] = []
    crops: dict[str, bytes] = {}
    for region in page.regions:
        region_items = segment_region(
            region=region,
            raster=prepared.raster,
            page_strokes=prepared.strokes,
            vision=vision,
            page_id=page.id,
            alignment_method=alignment.method,
            page_low_confidence=alignment.low_confidence,
            model=model,
            created_at=created_at,
        )
        for candidate, cand_strokes in region_items:
            crops[candidate.id] = _crop_png(prepared.raster, candidate.bbox)
            items.append((candidate, cand_strokes))

    provenance = CandidateProvenance(
        source_page_id=page.id,
        source_raster=source_raster,
        source_svg=source_svg,
        source_rm=source_rm,
        alignment_method=alignment.method,
        alignment_residual_px=alignment.residual_px,
        model=model,
    )
    cs = write_candidate_set(out_dir, provenance, items, crops=crops, force=force)
    if debug_dir is not None:
        write_debug_overlay(debug_dir, page, prepared, [c for c, _ in items])
    return cs


def write_debug_overlay(
    debug_dir: str | Path, page: Page, prepared: PreparedPage, candidates: list[Candidate] = ()
) -> Path:
    debug_dir = Path(debug_dir)
    debug_dir.mkdir(parents=True, exist_ok=True)
    overlay = render_overlay(
        prepared.raster,
        strokes=prepared.strokes,
        regions=page.regions,
        candidates=candidates,
        fiducials=page.fiducials,
        measured=prepared.measured,
    )
    out = debug_dir / f"{page.id}-overlay.png"
    overlay.save(out)
    return out


def _build_vision_client(model: str) -> VisionFn:
    """Construct the real Claude vision client (reads ANTHROPIC_API_KEY via the SDK)."""
    import anthropic

    return ClaudeVisionClient(anthropic.Anthropic(), model=model)


_PAGE_IN_NAME = re.compile(r"page[\s_-]*(\d+)", re.IGNORECASE)


def page_index_from_name(path: str | Path) -> int | None:
    """The 0-based page index implied by an export filename like 'capture_pdf - page 3.svg'."""
    m = _PAGE_IN_NAME.search(Path(path).stem)
    if not m:
        return None
    n = int(m.group(1))
    return n - 1 if n >= 1 else None


def _load_export(args: argparse.Namespace, page: Page) -> RemarkableExport | None:
    if args.svg:
        return load_remarkable_export(args.svg)
    if args.rm:
        from ingest_segment.remarkable_rm import load_rm_export

        template = Path(args.template).read_bytes() if args.template else None
        return load_rm_export(args.rm, (page.width_px, page.height_px), template=template)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Segment a written-on capture page into candidate glyph samples."
    )
    parser.add_argument("--sidecar", required=True, help="capture.sidecar.json (Contract X)")
    parser.add_argument("--out", required=True, help="output CandidateSet directory")
    src = parser.add_argument_group("page input (give --svg, --rm, and/or --raster)")
    src.add_argument("--svg", default=None, help="reMarkable SVG export (self-contains the page raster + ink)")
    src.add_argument("--rm", default=None, help="reMarkable native .rm page (needs ingest-segment[rm])")
    src.add_argument("--template", default=None, help="rendered template page PNG, used as the raster under --rm ink")
    src.add_argument("--raster", default=None, help="page raster PNG (raster-only ingest, or override of the export's raster)")
    parser.add_argument("--page-index", type=int, default=None, help="page in the sidecar (default: from the export filename, else 0)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing output dir")
    parser.add_argument("--model", default=VISION_MODEL, help=f"vision model (default: {VISION_MODEL})")
    parser.add_argument("--created-at", default=None, help="ISO timestamp stamped on candidates")
    parser.add_argument("--debug-dir", default=None, help="write a per-page overlay PNG (strokes, regions, candidates) here")
    parser.add_argument("--dry-run", action="store_true", help="adapter + alignment only, no vision calls; writes the overlay to --debug-dir (default: <out>-debug)")
    args = parser.parse_args(argv)

    if not (args.svg or args.rm or args.raster):
        print("provide --svg or --rm (a reMarkable export) and/or --raster")
        return 1
    if not args.dry_run and Path(args.out).exists() and not args.force:
        print(f"output dir already exists: {args.out} (use --force to overwrite)")
        return 1

    sidecar = CaptureSidecar.model_validate_json(Path(args.sidecar).read_text(encoding="utf-8"))
    page_index = args.page_index
    if page_index is None:
        page_index = page_index_from_name(args.svg or args.rm or args.raster) or 0
    if page_index < 0 or page_index >= len(sidecar.pages):
        print(f"page index {page_index} out of range (sidecar has {len(sidecar.pages)} pages)")
        return 1
    page = sidecar.pages[page_index]

    export = _load_export(args, page)
    if export is not None:
        print(f"reMarkable export: {export.summary()}")

    if args.raster:
        raster = load_raster(args.raster)
        if export is not None and raster.size != export.page_size:
            print(
                f"--raster size {raster.size} != export page size {export.page_size}; "
                "the two are in different coordinate spaces and cannot be combined"
            )
            return 1
    else:
        raster = export.page_raster
    strokes = export.strokes if export is not None else []

    if raster.size != (page.width_px, page.height_px):
        print(
            f"note: export size {raster.size} != sidecar page size ({page.width_px}, {page.height_px}); "
            "raster and strokes are warped into page space by the alignment."
        )

    if args.dry_run:
        prepared = prepare_page(page, raster, strokes)
        a = prepared.alignment
        res = f"{a.residual_px:.2f} px" if a.residual_px is not None else "n/a"
        print(
            f"alignment: {a.method} (residual {res}, fiducials found {len(prepared.measured)}/{len(page.fiducials)}"
            f"{', LOW CONFIDENCE' if a.low_confidence else ''})"
        )
        out = write_debug_overlay(args.debug_dir or f"{args.out}-debug", page, prepared)
        print(f"dry run: wrote overlay {out}; no vision calls made, no candidates written.")
        return 0

    created_at = args.created_at or "1970-01-01T00:00:00Z"
    cs = ingest_page(
        page=page,
        raster=raster,
        strokes=strokes,
        vision=_build_vision_client(args.model),
        model=args.model,
        created_at=created_at,
        out_dir=args.out,
        source_raster=Path(args.raster).name if args.raster else None,
        source_svg=Path(args.svg).name if args.svg else None,
        source_rm=Path(args.rm).name if args.rm else None,
        force=args.force,
        debug_dir=args.debug_dir,
    )

    print(
        f"Wrote {len(cs.candidates)} candidate(s) to {args.out} "
        f"(alignment: {cs.provenance.alignment_method})."
    )
    for c in cs.candidates:  # already sorted lowest-confidence-first
        why = f"  [{', '.join(c.review_reasons)}]" if c.review_reasons else ""
        print(f"  {c.confidence:.2f}  {c.status.value:13s}  {c.label!r} ({c.kind.value}){why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

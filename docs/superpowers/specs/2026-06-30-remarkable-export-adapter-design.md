# reMarkable Export Adapter — Module Design

**Date:** 2026-06-30
**Status:** Approved for planning
**Module:** `ingest-segment` input adapter (replaces `svg_strokes.py`)
**Depends on:** `hwfont-schema` (`StrokeData`/`Contour`/`StrokePoint`), Pillow, numpy, `scikit-image` (new), an SVG/XML parser
**Parent spec:** [ingest-segment](2026-06-18-ingest-segment-design.md) (this realizes that spec's quarantined "input adapter" against a real device export)

## Purpose

Ingest a **real reMarkable SVG export** of a written-on capture page and produce exactly what the existing `ingest-segment` pipeline (`align → segment → candidates_out`) already consumes: a **page raster** in Contract X page-pixel space, and **per-stroke centerline geometry** (`StrokeData`-style contours) in that same space. This replaces the synthetic, stroke-colored SVG assumption baked into `svg_strokes.py` — which the real device does not produce — with the device's actual format.

## Why this is needed (real-export teardown)

A real export (`capture_pdf - page 1.svg`, 309 KB) of a `capture-template` page written on a reMarkable revealed the original `svg_strokes.py` assumptions are wrong in three concrete ways — exactly the "Known Risk" the parent spec isolated:

| `svg_strokes.py` assumption | Reality in the export |
| --- | --- |
| Top-left origin `(0,0)` | **Centered x-origin**: `viewBox="-705 0 1410 1880"` (x runs −705…+705); ink points have negative x |
| Ink = `stroke="<dark>"` polylines | Ink = **270 `fill="#000000"` filled contour paths** (the filled *outline* of each pen trace), **zero `stroke` attributes** → the old luminance-of-stroke separation would discard all ink |
| Template separable from ink by path color | Template (faint prompts, ruled lines, **the 4 corner fiducials**) is baked into an embedded raster `<image>` (full-page PNG, ~0.07% dark pixels); the **handwriting ink is only in the vector paths**, not the raster |

Two key consequences drive the design:
1. The export gives two complementary layers — a **template raster** (carries the fiducials we align against) and **vector ink** (carries the handwriting). Neither alone is a usable page image; they must be composited.
2. Each `<path>` is the filled outline of **one pen stroke** (the reMarkable's natural per-stroke segmentation). Extracting the pen centerline therefore means skeletonizing each filled shape **independently** — we never have to disentangle crossings *between* strokes, only the rare self-crossing *within* one stroke.

Vision already works on this data: the live Claude Opus 4.8 call returned 46/46 units with a perfect label match against the known transcript on the first real handwriting line. The gap is purely the input adapter.

## Design Decisions (settled during brainstorming)

1. **Fidelity:** extract **true pen centerlines** now (not raster-only deferral, not raw outline-as-stroke), so `Candidate.StrokeData` carries real stroke geometry for `font-gen`.
2. **Centerline method:** per-path **raster skeletonization** (rasterize the filled path → medial-axis skeleton → trace → simplify), via `scikit-image`. Chosen over pure-vector outline-pairing (fragile on loops/cusps) and whole-page distance-transform ridges (would merge touching strokes and lose the per-stroke segmentation the device hands us for free).
3. **Module scope:** **replace** `svg_strokes.py` with a new `remarkable_svg.py`. The stroke-colored format never existed, so its code and `test_svg_strokes.py` are retired rather than maintained alongside.
4. **Page raster:** **composite** the normalized ink (solid black) over the decoded template raster → one page raster carrying prompts + fiducials + handwriting, serving both fiducial detection and per-region vision crops.
5. **Downstream untouched:** `align.py`, `segment.py`, `candidates_out.py`, and `run.ingest_page` are unchanged; the adapter produces the `(raster, strokes)` they already accept.

## Architecture

New module `packages/ingest-segment/src/ingest_segment/remarkable_svg.py` replaces `svg_strokes.py`. One entry point plus small, independently-testable helpers:

```
load_remarkable_export(svg_path) -> RemarkableExport
    .page_raster    : PIL.Image.Image    # template raster + composited ink, page-px
    .strokes        : list[list[tuple[float, float]]]  # centerlines in page-px, one per pen stroke
    .page_size      : tuple[int, int]     # (w, h) from viewBox
    .viewbox_offset : tuple[float, float] # (dx, dy) normalization applied
    .dropped_paths  : int                 # degenerate paths skipped (reported)
    .has_template   : bool                # False -> ink-on-white fallback raster
```

Internal helpers:
- `parse_svg(svg_path)` → `(viewbox, template_png_bytes | None, fill_rings)` where `viewbox = (minX, minY, w, h)` and `fill_rings` is a list of point-lists (one per `<path>`, in viewBox coords).
- `normalize(points, viewbox)` → points translated by `(-minX, -minY)` into page-px.
- `centerline(ring, page_size)` → one ordered, simplified centerline polyline (the skeletonization core), or `None` if degenerate.
- `compose_raster(template_png | None, strokes, page_size)` → the page raster handed to the pipeline.

**Data flow:**

```
reMarkable SVG export (one page)
  → parse_svg: viewBox + embedded template PNG + fill-contour rings
  → normalize rings by -viewBox.min → page-px ink outlines
  → per ring: centerline() = rasterize fill → skeletonize → trace → simplify → centerline polyline
  → compose_raster: composite ink (black) over decoded template raster (or ink-on-white if no template)
  → RemarkableExport(page_raster, strokes, ...)
  → run.ingest_page(page=<sidecar page>, raster=page_raster, page_strokes_export=strokes, vision=..., ...)
  → existing pipeline: detect_fiducials → align → segment (Claude vision) → CandidateSet
```

## Coordinate Normalization

Parse `viewBox="minX minY w h"`. Translate every ink point and align the page raster by `(x - minX, y - minY)`. For the sample export that is `+705` in x (centered `−705…705` → `0…1410`), `0` in y. General for any `viewBox`. The embedded `<image>`'s placement (`x=-705, y=0`) confirms the same offset, so the decoded template PNG's pixels already align at `(0,0)`. Page size = `(round(viewBox.w), round(viewBox.h))`, cross-checked against the decoded PNG dimensions; mismatch beyond a small tolerance is logged. If `viewBox` is absent, offset is `(0,0)` and size comes from the root `width`/`height` attributes.

## Centerline Extraction (`centerline`, the core)

Per fill-path ring (already normalized to page-px):
1. **Rasterize** the filled polygon into a tight binary mask with a small padding margin (`PIL.ImageDraw.polygon`, `fill-rule` evenodd approximated by even-odd polygon fill), remembering the mask's page-px origin `(ox, oy)`.
2. **Skeletonize** with `skimage.morphology.skeletonize` → 1-px-wide skeleton.
3. **Trace** the skeleton into an ordered point sequence:
   - Build a pixel adjacency graph (8-connectivity).
   - **Open curve** (exactly 2 endpoints, degree-1 pixels): walk endpoint → endpoint.
   - **Branched** (a self-touching stroke produces >2 endpoints): take the **longest endpoint-to-endpoint path**.
   - **Closed loop** (no degree-1 endpoints, e.g. a fully closed "o"): cut at the topmost-leftmost pixel and walk the cycle back to the cut.
4. **Simplify** with Douglas–Peucker (epsilon ≈ 1 px) to drop redundant collinear points; offset back by `(ox, oy)` into page-px → one ordered contour.
5. **Degenerate handling:** a path below a minimum filled-area / skeleton-length threshold (i-dots, periods, pen taps) collapses to a **2-point stub** at its centroid so it still round-trips as a valid `Contour` (which requires ≥2 points). A zero-area / empty-skeleton path returns `None` and is counted in `dropped_paths`.

Pen **direction** is not recoverable from a filled outline, so the start of a stroke is a deterministic choice (topmost-leftmost endpoint), documented. `pressure` is `None` (raster-sourced), matching today's `StrokeData`. Cross-stroke ordering is document order (best-effort, ≈ pen order on the reMarkable).

## Page Raster for the Pipeline (`compose_raster`)

Composite the normalized ink, rendered solid black, **over** the decoded template raster → one page raster carrying faint prompts + the 4 corner fiducials + black handwriting. This single raster serves both `detect_fiducials` (fiducials come from the template layer) and the per-region vision crops.

This relies on Contract X region bboxes covering the **writing area only** — `capture-template` prints each prompt *above* the writing line (in `prompt_gap`), so a region crop contains the handwriting and ruled line, not the printed prompt. If that assumption is ever violated (printed prompt bleeding into crops, confusing vision), the documented fallback is an ink-on-white render for crops; not built now (YAGNI).

If the SVG has **no** embedded `<image>` (`has_template = False`), the page raster is an ink-on-white render at `page_size`; fiducials won't be detectable, so `align` falls back to geometric-scale with low confidence (already implemented). This is logged.

## Error Handling

Rule: flag/report, never silently mislead.
- No embedded `<image>` → ink-on-white raster, `has_template = False`, logged; downstream alignment degrades to geometric-scale + `needs_review`.
- No fill paths → error (the page carries no ink; nothing to segment).
- `viewBox` absent → offset `(0,0)`, size from `width`/`height`.
- Degenerate / empty-skeleton path → dropped, surfaced via `dropped_paths` (and the CLI summary).
- Decoded PNG size vs `viewBox` size mismatch beyond tolerance → logged (alignment still proceeds; the affine absorbs small scale differences).
- `scikit-image` not importable → clear, early error at adapter entry.
- **Non-determinism:** rasterization + skeletonization are deterministic, so the adapter's `(raster, strokes)` output is reproducible (unlike the vision step downstream).

## CLI / Integration

A reMarkable export is self-contained, so `--raster` becomes **optional**. New usage:

```
ingest-segment --svg <export.svg> --sidecar <capture.sidecar.json> --out <dir> [--page-index N] [--force]
```

When `--svg` is a reMarkable export, the CLI calls `load_remarkable_export`, then feeds `.page_raster` and `.strokes` into the unchanged `ingest_page`. `--raster` may still be supplied to override the extracted raster. `--page-index` selects which sidecar page this single-page SVG corresponds to (the device exports one SVG per page). The CLI summary reports `dropped_paths` and whether the template/fiducials were found.

Running a **full** pipeline on a real page still requires a matching Contract X **sidecar** (regions, expected transcripts, fiducial positions). Generating/reconstructing that sidecar for an already-written booklet is **out of scope** for this module (its own later cycle); this adapter's responsibility ends at producing the page raster + centerline strokes.

## Testing Strategy (TDD)

Mirror `ingest-segment`'s mock-unit + gated-real pattern. Replace `test_svg_strokes.py` (retired format) with `test_remarkable_svg.py`.

- **`parse_svg`:** a crafted SVG (centered `viewBox`, a small embedded PNG with 4 corner dots, two `fill` paths) → viewBox parsed, template bytes decoded, 2 rings returned in viewBox coords.
- **`normalize`:** centered viewBox → points shifted into `0…w` page-px.
- **`centerline`:** a filled rectangle (thick horizontal bar) → a roughly horizontal mid-height polyline spanning the bar length; a filled disc → a short centroid stub; a curved filled bar → a monotone centerline following the curve; a degenerate sliver → `None` (counted).
- **`compose_raster`:** ink darkens the expected pixels over the template; the 4 fiducials survive in the result.
- **`load_remarkable_export` (end-to-end, synthetic):** a tiny synthetic reMarkable-style export → correct `page_size`, stroke count, page-px coordinate ranges, `has_template = True`, `dropped_paths` count.
- **No-template fallback:** an export with no `<image>` → ink-on-white raster, `has_template = False`.
- **Opt-in real-export test:** gated like the vision integration test — runs only when a real export path is provided (env var / known location), so personal handwriting is **not** committed to the repo. Asserts strokes extracted, page raster sized to the viewBox, and fiducials detectable on the composited raster.

## Technology

- **SVG/XML parsing** — `lxml` or stdlib `xml.etree` for `viewBox` / `<image>` / `<path>`; path `d` parsing for `M`/`L` rings (the export uses only line segments). Reuse `svgpathtools` if convenient.
- **Raster** — Pillow (`ImageDraw.polygon` rasterization, base64 PNG decode of the embedded image, compositing).
- **Skeletonization** — `scikit-image` (`morphology.skeletonize`); `scipy` (already transitively present) for graph/labeling helpers.
- **numpy** — mask/graph arrays.
- pytest; synthetic fixtures committed, one opt-in real-export test.

## Out of Scope

- Contract X **sidecar regeneration/reconstruction** for an existing booklet (separate cycle).
- `font-gen` autotrace and the raster-only stroke path.
- Native `.rm` parsing.
- Pen **pressure**, true stroke **direction**, and temporal ordering beyond document order.
- Multi-page-per-file (the device exports one SVG per page; one file = one page).

## Known Risk

The teardown is based on a single real export. Other reMarkable firmware/export settings may vary the SVG shape (e.g., `stroke`-based ink, different `viewBox` conventions, or ink baked into the raster). The adapter is isolated behind `load_remarkable_export` with small swappable helpers and is validated by both synthetic fixtures and an opt-in real-export test, so a future format variant is a localized change. Skeletonization quality on dense cursive (heavy self-overlap within a single pen stroke) is the main fidelity unknown and is the first thing to evaluate against more real pages.

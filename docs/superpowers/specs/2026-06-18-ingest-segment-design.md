# ingest-segment — Module Design

**Date:** 2026-06-18
**Status:** Approved for planning
**Module:** `ingest-segment` (README Component 2a / architecture Module 2)
**Depends on:** `hwfont-schema` (Contract X, plus a new `Candidate` contract added here) and the Anthropic SDK (Claude Opus 4.8 vision)
**Parent spec:** [architecture](2026-06-15-handwriting-font-architecture-design.md)
**Downstream:** `review` (consumes the `CandidateSet`; its own later cycle)

## Purpose

Turn a written-on capture page — exported from the reMarkable — plus its Contract X sidecar into **candidate glyph samples**: proposed-but-unconfirmed glyphs/ligatures, each with a label, kind, confidence, bounding box, the ink strokes inside it, a crop image, and transcript-derived context. Claude Opus 4.8 (vision) does the automated first pass against the *known* prompt text; a human confirms/corrects later in `review`. This is the segmentation step the architecture spec deferred.

## Design Decisions (settled during brainstorming)

1. **Scope:** `ingest-segment` first, on its own; `review` is a separate later cycle. The `CandidateSet` this module emits is the contract `review` consumes.
2. **Input format:** SVG (vector ink) **primary** + raster PNG **fallback**; native `.rm` is a future fidelity upgrade. The raster is always required (vision input + fiducial detection); SVG is optional and adds real stroke geometry.
3. **Candidate artifact:** a new `Candidate` / `CandidateSet` contract in `hwfont-schema`, alongside Contracts X and Y. Keeps the un-normalized, confidence-bearing candidate cleanly separate from the normalized `Sample`.
4. **Alignment:** fiducial registration marks as the backbone (printed by `capture-template`, detected here), geometric-scale fallback when marks aren't found, and human nudge in `review` as the last resort.
5. **Vision:** Claude Opus 4.8, per-region crop + expected transcript + structured output (`{label, kind, bbox, confidence}`), per the architecture spec. Mocked in unit tests; one opt-in integration test.

## Precursors (early tasks in this module's plan)

- **`hwfont-schema`:** add the `Candidate` / `CandidateSet` contract (below); add `fiducials` (known corner-mark positions) to Contract X's `Page`.
- **`capture-template`:** print 4 corner fiducial marks at known page positions and record them in the sidecar (`Page.fiducials`).

## Architecture

New package `packages/ingest-segment/`, depending only on `hwfont-schema` plus the Anthropic SDK, an SVG parser, and Pillow. Files:

- `svg_strokes.py` — parse the SVG export into per-stroke geometry (ordered points), separating the writer's ink from the printed template.
- `raster.py` — render/load a flattened page raster (vision crops + fiducial detection); the raster-only fallback path.
- `align.py` — detect fiducials in the raster → affine (export→page-px); geometric-scale fallback; transform strokes into page-pixel space; record alignment provenance.
- `segment.py` — per Contract X region: crop the raster, call Claude Opus 4.8 with the crop + expected transcript + structured-output schema → labeled boxes; map boxes onto aligned strokes → candidates; derive context from the transcript.
- `candidates_out.py` — write/read the `CandidateSet` (JSON manifest + per-candidate stroke/crop files).
- `run.py` + CLI — orchestrate parse → align → segment → emit; report; order candidates lowest-confidence first.

**Data flow:**

```
page export (raster PNG [+ optional SVG ink]) + capture.sidecar.json (Contract X)
  → svg_strokes (ink) + raster (page image)
  → align: detect fiducials → affine → strokes & raster in page-pixel space (+ provenance)
  → per Contract X region:
        crop raster → Claude Opus 4.8 (crop + expected transcript + schema)
        → [{label, kind, bbox(crop px), confidence}]
        → map crop bbox → page-px; assign aligned strokes; derive Context from transcript
        → Candidate
  → CandidateSet (JSON manifest + per-candidate strokes/crop)  → review (later)
```

## Input Parsing & Alignment

**Inputs per page:** a **raster** (PNG, or rasterized from a combined PDF via `cairosvg`/equivalent when only an SVG is supplied) — always required — and an **optional SVG** providing ink stroke vectors. No SVG → raster-only candidates (no strokes; `font-gen` autotraces later). 

**Ink vs. template separation (`svg_strokes`):** the export contains both the printed template (prompts, rules, fiducials) and the writer's ink. Separate ink primarily by stroke attributes (template rendered light gray ~0.6–0.8; ink near-black), cross-checked against the known template geometry from the sidecar. This is the most assumption-dependent function — isolated behind a clean interface, synthetic-fixture-tested, and the first thing to revisit against a real device export.

**Alignment (`align`):**
- Render the export to the sidecar's page-pixel dimensions, then detect the 4 fiducial marks (blob/template-match within expected corner regions) → measured positions.
- Compute a similarity affine (scale + translate, tolerating small rotation) mapping measured → expected (`Page.fiducials`). Apply to the raster (for region crops) and to the SVG strokes (composed with the export→raster scale) — all landing in **Contract X page-pixel space** (top-left, px@dpi), un-normalized.
- **Fallback chain:** fiducials not found / too few / low-confidence → geometric-scale by page dimensions; residual error above threshold → page flagged `low-confidence alignment`, its candidates `needs_review`.
- Alignment method + residual recorded as `CandidateSet` provenance.

## Segmentation (Claude vision)

Per Contract X region:
1. Crop the aligned page raster to the region bbox → region crop (PNG).
2. Call Claude Opus 4.8 (vision) with the crop image, the region's **expected transcript**, its `expected_units`/`ligature_targets`, and a structured-output schema returning `list[{label, kind, bbox(crop px), confidence: 0..1}]`, ordered left-to-right. Prompt: "this region was written from known text *T* — locate and label each unit you were asked to capture; boxes in crop pixel coordinates." Adaptive thinking, `effort: high`.
3. Validate against the schema (SDK-forced); cross-check returned labels against the expected sequence — count mismatch / low confidence / zero boxes → flag those candidates `needs_review`.
4. Map each crop bbox → page-px (offset by crop origin); assign aligned strokes whose extent falls mostly inside the bbox; a straddling stroke goes to the majority-overlap box and flags the candidate low-confidence; unassigned strokes flagged.
5. Derive `Context` (left/right neighbor, `position_in_word`) from the known transcript sequence — context is *known*, not guessed, and matches what Contract Y's `Sample` needs.

The vision call is **mocked** in unit tests (prompt/crop construction, schema-validated parsing, mapping, flagging); one **env-gated integration test** hits real Opus 4.8 on a sample crop.

## The `Candidate` Contract (`hwfont-schema` addition)

- `CandidateStatus` enum: `pending`, `needs_review`.
- `Candidate` (pydantic): `id`, `page_id`, `region_id`, `label`, `kind: Kind`, `confidence: float` (0–1), `bbox: BBox` (page-px), `context: Context` (reused from Contract Y), `strokes_path: str | None`, `crop_path: str | None`, `status: CandidateStatus`, `alignment_method: str`, `model: str`, `created_at: str` (ISO, caller-supplied).
- `CandidateSet`: a directory + **JSON manifest** (`candidates.json`) listing candidates plus a top-level provenance block (alignment method/residual, source page/export, model), with per-candidate `strokes/<id>.json` (`StrokeData`) and `crop/<id>.png` files. JSON (not SQLite) because candidates are a transient per-session working set; sorted lowest-confidence-first for review.
- Reuses `Context`, `BBox`, `Kind`, `StrokeData` from `hwfont-schema`. On accept (in `review`, later), a candidate's page-px geometry is normalized to em-space and written as an accepted `Sample` into the glyph store.

## Error Handling

Rule: flag, never silently drop.
- Raster missing → error. SVG parse failure → raster-only fallback (no strokes), logged. Sidecar missing/invalid → error (validated against `hwfont_schema`). Page-count/dimension mismatch between sidecar and export → error.
- Fiducials not found/too few/low-confidence → geometric-scale fallback (in provenance); residual above threshold → page `low-confidence alignment`, candidates `needs_review`.
- Vision API errors → SDK retry/backoff; schema/label-count mismatch, zero boxes, or low confidence → candidates `needs_review`, never silently skipped.
- Straddling/unassigned strokes flagged.
- Output dir overwrite refused without `--force`.
- **Non-determinism:** vision is non-deterministic, so output is not byte-reproducible (unlike `capture-template`) — documented; tests pin behavior with mocked responses.

## Testing Strategy (TDD)

- **`hwfont-schema` `Candidate`:** model validation, JSON round-trip, `CandidateSet` manifest + per-candidate sidecar read/write.
- **`capture-template` fiducials:** marks at known positions; `Page.fiducials` populated; geometry matches.
- **`svg_strokes`:** parse synthetic SVG → strokes; ink/template separation on a gray-template + dark-ink fixture → only ink.
- **`raster`:** fiducial detection on a synthetic raster with known corner marks → measured ≈ expected.
- **`align`:** recover a known affine from transformed marks; geometric-scale fallback; residual-threshold flagging.
- **`segment`:** mocked vision client returning a fixed structured response → crop construction, schema parse, crop→page mapping, stroke assignment, transcript-derived context, and each `needs_review` flag path; one env-gated integration test on real Opus 4.8.
- **`candidates_out` + end-to-end:** a synthetic export (SVG + raster + matching sidecar, generated from a `capture-template` page with synthetic ink) → run with mocked vision → `CandidateSet` validates, candidates carry context/strokes/confidence, ordered lowest-confidence-first, flags correct.

## Technology

- **Anthropic SDK** — Claude Opus 4.8, vision (image input), structured outputs (`output_config.format`), adaptive thinking, `effort: high`.
- **SVG parsing** — `svgpathtools` (or `lxml` + `svg.path`).
- **Raster** — Pillow (crop + fiducial blob detection); `cairosvg` only to rasterize an SVG-only input.
- **`hwfont-schema`** — Contract X, the new `Candidate` contract, and `Context`/`BBox`/`Kind`/`StrokeData`.
- pytest with a mocked Anthropic client; one opt-in (env-gated) real-API integration test.

## Out of Scope

- `review` (the human-in-the-loop tool that consumes the `CandidateSet`) — its own cycle.
- Normalization to em-space and writing `Sample`s to the glyph store — happens in `review` on accept.
- Native `.rm` parsing (future fidelity upgrade).
- Font generation.

## Known Risk

No real reMarkable export exists yet, so the SVG's internal structure (ink/template separation, fiducial visibility, coordinate space) is an **assumption**. The input adapter (`svg_strokes`/`raster`/`align`) is isolated behind a clean interface and built/tested against synthetic fixtures, so the pipeline is fully testable offline and the adapter is swappable once a real export is available.

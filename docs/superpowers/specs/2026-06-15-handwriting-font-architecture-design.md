# Handwriting Font — System Architecture Design

**Date:** 2026-06-15
**Status:** Approved for planning
**Scope:** Overall architecture and the data contracts connecting the two README components. Detailed internal designs for each module are deferred to their own spec → plan cycles.

## Purpose

Turn a person's handwriting, captured on a reMarkable Paper Pro (or printed and scanned), into a feature-rich OpenType font with contextual alternates, extended ligatures, and glyph cycling for natural variation. This document defines how the system is decomposed and, specifically, the two well-defined contracts that let the capture side and the font side be built and tested independently.

## Design Decisions (settled during brainstorming)

1. **Segmentation strategy:** Claude vision performs an automated first pass; a human-in-the-loop review tool confirms/corrects before any data reaches the font generator. The vision pass always receives the *expected transcript* for each region, turning open-ended recognition into constrained labeling-against-known-text.
2. **Hand-off artifact:** A rich, context-tagged glyph store — each sample carries its label, ink (stroke geometry), surrounding context, metrics, and a quality flag. This is what makes contextual alternates, extended ligatures, and intelligent cycling possible.
3. **Source-of-truth representation:** Vector strokes (reMarkable native export) are primary; raster (printed-and-scanned) is a degraded fallback path.
4. **Review tool form factor:** Local web app (Python/FastAPI backend + browser canvas), decoupled from the Python modules behind a thin HTTP/JSON boundary.
5. **Vision model:** Claude Opus 4.8 (`claude-opus-4-8`) — image input, adaptive thinking, structured outputs, high-resolution image support.

## Architecture Overview

Five focused modules plus a shared contract package. The README's two components are Modules 1 and 4. The two **contracts** (X and Y) are the hand-offs that allow independent development.

```
capture-template (M1) ──emits PDF + sidecar──▶ [Contract X: capture sidecar]
        │
        ▼  (human writes on reMarkable → stroke export; scan = raster fallback)
ingest-segment (M2) ──uses Contract X + Claude vision──▶ candidate samples
        │
        ▼
review (M3) ──human confirms/corrects, normalizes──▶ [Contract Y: glyph store]
        │
        ▼
font-gen (M4) ──reads store──▶ .otf / .ttf

hwfont-schema (M0): defines Contracts X & Y. Every other module depends on this
                    and on nothing else of each other.
```

## Modules

### Module 0 — `hwfont-schema` (shared contract)
The only shared code. Defines the data models, the SQLite schema, and the on-disk layout for both contracts, plus validators. Every other module imports this and nothing else of each other, so capture and font generation are fully decoupled.

### Module 1 — `capture-template` (README Component 1)
Takes a *prompt corpus* and a layout config; emits a **PDF** for writing and a **sidecar** (Contract X) describing what each region should contain and where. Internal concern: a corpus picker that selects prompt sentences guaranteeing coverage — every target glyph at least a dozen times, target ligatures many times.

### Module 2 — `ingest-segment`
Parses the reMarkable export into per-stroke geometry grouped by page (raster scan path yields only the image). For each region in Contract X, renders a crop and calls Claude Opus 4.8 with the crop, the expected transcript, and a structured-output schema demanding one entry per glyph/ligature (`{label, kind, bbox, confidence}`). Maps each returned box back onto the strokes inside it to form a candidate sample carrying real stroke geometry. Emits candidates with confidence + provenance, lowest-confidence first.

### Module 3 — `review` (human-in-the-loop)
Local web app. Loads candidates over the page image with proposed boxes/labels; the human confirms, nudges a box or stroke assignment, fixes a label, or flags quality. On accept, the sample is normalized to baseline/em space and written into the glyph store (Contract Y). A live coverage panel shows progress per target (`a: 14/12 ✓`, `eft: 7/many`).

### Module 4 — `font-gen` (README Component 2)
A pure function of the store; never knows how capture or segmentation happened.
- **Outlines from strokes:** model a pen nib (width scales with pressure if present, fixed otherwise), offset each centerline stroke into a contour, union per glyph, fit Béziers. Raster-sourced samples fall back to autotracing.
- **Glyph cycling:** keep N accepted variants per character (`a`, `a.alt1`, …); a `calt` cascade rotates through the ring based on preceding context so repeats look varied.
- **Contextual alternates:** `position_in_word` + neighbor fields drive `calt` rules selecting initial/medial/final forms.
- **Ligatures:** each captured ligature (standard `fi`/`ffi` and extended `eft`/`fore`/…) becomes a glyph mapped via `liga`/`dlig` + contextual rules.
- **Metrics & compile:** advance widths and side bearings from per-sample metrics; baseline/x-height/cap-height in the header; compile via `fontTools`/`feaLib` to `.otf` (and optionally `.ttf`).

## Contracts

### Contract X — capture sidecar (`capture-template` → `ingest-segment`)
A versioned JSON file emitted alongside the PDF.
- `page[]`: page id, pixel size / dpi, source bounds.
- `region[]` per page: the **expected transcript** (e.g. `"the quick brown fox"`), ruled-row geometry (baseline + bounding box in page coordinates), the ordered list of glyphs/ligatures the row is meant to capture, and which ligature targets appear there.

Purpose: hand `ingest-segment` "this rectangle should contain text *T* on baseline *B*" — collapsing recognition into alignment.

### Contract Y — glyph store (`review` → `font-gen`)
A directory plus a **SQLite manifest** (`store.db`) and per-sample files.
- `sample` row: `id`; `label` (grapheme or ligature string, e.g. `a`, `eft`); `kind` (`single` | `ligature`); refs to a **strokes file** (ordered contours of points, optional pressure) and a **normalized raster** (PNG, for vision/preview); `context` (source word, left/right neighbor glyph, `position_in_word` = initial/medial/final/isolated); `metrics` (baseline, x-height, advance, bbox — all em-normalized); `quality` flag; `review_status`; capture-session id; timestamp.
- `target` table: the catalog of wanted glyphs + ligatures with required counts → drives coverage reporting.

Two deliberate choices: **normalization happens at review-write time** (so `font-gen` never touches page geometry), and the **manifest is SQLite** (constant querying: "all `a` in final position", coverage counts, dedupe), while per-sample ink lives in sidecar files, not blobs.

## Error Handling

Rule: **flag, never silently drop.**
- **Contract boundaries:** validators in `hwfont-schema`; every module validates at its inputs/outputs and fails fast with a clear message.
- **`ingest-segment`:** box-count mismatch vs. expected transcript, low confidence, or a stroke straddling two boxes → candidate marked **needs-review**, not discarded. API calls use the SDK's retry/backoff.
- **`review`:** cannot write a sample that fails schema validation; coverage panel warns when a target is under its required count.
- **`font-gen`:** a target with zero accepted samples, or a degenerate/self-intersecting outline → recorded in a **QA report**; the build never emits a quietly broken font.

## Testing Strategy (TDD throughout)

- **`hwfont-schema`:** model validation + serialize/deserialize round-trips.
- **`capture-template`:** sidecar geometry matches the rendered PDF; corpus-coverage assertion (every target meets its required count in the chosen sentences).
- **`ingest-segment`:** stroke parsing and bbox→stroke mapping on synthetic fixtures; the Claude call is **mocked** in unit tests (assert schema-validated parse + low-confidence flagging), with one opt-in integration test hitting the real API on a sample page.
- **`review`:** normalization math and accept→store-write tested headless; UI kept thin.
- **`font-gen`:** build a font from a fixture store, reload with `fontTools`, assert glyph count, `calt`/`liga` presence, and advance widths; snapshot a rendered test string.

## Technology

- **Language:** Python (per README).
- **Font assembly:** `fontTools` / `feaLib`; custom pen-nib stroking module.
- **PDF/template:** LaTeX → PDF (per README).
- **Vision:** Anthropic SDK, Claude Opus 4.8, structured outputs (`output_config.format`), adaptive thinking, `effort: high`.
- **Review app:** FastAPI backend + browser canvas frontend.
- **Store:** SQLite manifest + per-sample stroke/raster files.

## Out of Scope (this spec)

- Detailed internal design of each module (each gets its own spec → plan).
- Corpus-selection algorithm specifics.
- reMarkable export format parsing details (resolved when building `ingest-segment`).
- Font distribution/packaging and variable-font axes.

## Build Order

Recommended sequence, each as its own spec → plan → implementation cycle:
1. `hwfont-schema` (both contracts) — unblocks everything.
2. `capture-template` — needed to produce real capture material.
3. `ingest-segment` + `review` — the segmentation pipeline.
4. `font-gen` — consumes the store.

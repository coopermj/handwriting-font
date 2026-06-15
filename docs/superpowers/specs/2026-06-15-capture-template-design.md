# capture-template — Module Design

**Date:** 2026-06-15
**Status:** Approved for planning
**Module:** `capture-template` (README Component 1, Module 1 in the architecture spec)
**Depends on:** `hwfont-schema` (for `Target` and the Contract X `CaptureSidecar`/`Page`/`Region` models)
**Parent spec:** [2026-06-15-handwriting-font-architecture-design.md](2026-06-15-handwriting-font-architecture-design.md)

## Purpose

Generate the handwriting-capture material: a **PDF** the user writes on (reMarkable Paper Pro, or print + scan) and a **Contract X sidecar** (`capture.sidecar.json`) describing what each writing region should contain and exactly where it sits on the page. The sentences are chosen to guarantee coverage — every target glyph and ligature captured at least its required number of times — sourced from classic literature and English speeches, with auto-generated drill lines filling any rare targets the prose can't supply.

## Design Decisions (settled during brainstorming)

1. **Row layout:** Each row prints the prompt sentence faintly above a **blank ruled writing line**; the user reads the prompt and writes it themselves on the empty line. This yields the most natural penmanship, and the writing line is a clean, predictable rectangle for the sidecar geometry. (The reMarkable keeps pen strokes on a separate layer, so the printed prompt never contaminates captured ink.)
2. **Coverage strategy:** Greedy selection of natural sentences to hit the common glyphs, then **auto-generated drill lines** for any target still under its required count (mostly the bespoke extended ligatures).
3. **Corpus sources:** Public-domain **classic literature** and **English speeches**, as plain-text files (a small bundled default set, plus an optional user-supplied directory).
4. **PDF generation:** **Direct PDF via ReportLab** — every element placed at exact coordinates, so the sidecar geometry is correct by construction. (Deliberate divergence from the README's "LaTeX template," chosen because the whole pipeline hinges on exact sidecar coordinates and this removes a TeX dependency and any LaTeX-vs-computed drift.)
5. **Target spec:** An **explicit, editable config** (YAML/JSON) listing glyphs and ligatures with required counts, with sensible defaults. Maps directly onto Contract Y's `target` table and the coverage report `hwfont-schema` already produces.

## Architecture Overview

One module, seven focused files, depending only on `hwfont-schema`. A single **layout model** produced by `layout.py` is the source of truth that both the PDF renderer and the sidecar emitter consume, so the rendered page and the sidecar geometry cannot drift apart.

```
target spec (config) ─┐
classic lit + speeches ┼─► corpus.py → candidate sentences
                       │        │
                       └────────┼─► planner.py → ordered prompt lines + coverage report
                                          │
                                          ▼
                                   layout.py  (single source of truth)
                                   → layout model (pages, rows, pixel-space geometry)
                                       │                          │
                                       ▼                          ▼
                                   pdf.py (ReportLab)        sidecar_out.py
                                   → capture.pdf             → capture.sidecar.json (Contract X)

generate.py / CLI orchestrates all of the above + writes targets.json (to seed a glyph store)
and prints the coverage report.
```

## Components

### `targets.py`
Load and validate the target-spec config (YAML/JSON) into a list of `hwfont_schema.Target` (`label`, `kind`, `required_count`). Ships defaults: ASCII upper/lower letters, digits, common punctuation at a default glyph count; standard ligatures (`fi`, `ffi`, …) plus a starter extended-ligature set at a default ligature count. Rejects unknown `kind`, non-positive counts, and duplicate labels.

### `corpus.py`
Load plain-text sources (bundled default corpus + optional user directory), split into candidate sentences with a lightweight rule-based splitter (no heavy NLP dependency), and filter to sentences whose characters are all within the target set. Deterministic ordering. Errors if the source is missing/empty or yields zero usable sentences.

### `planner.py`
Deterministic greedy coverage planner.
- Track a remaining *deficit* per target (starts at `required_count`).
- Score each candidate sentence by `Σ min(occurrences_in_sentence, remaining_deficit)`. Ligature occurrences are counted **non-overlapping, left-to-right**; glyph occurrences are character frequency.
- Repeatedly pick the highest-scoring sentence (ties broken by a stable key: shorter length, then lexicographic, then original index), append it, decrement deficits, remove it from the pool. Stop when all deficits are zero, no remaining sentence reduces the deficit, or a configurable line cap is reached.
- **Drill-fill:** for any target still short, generate targeted repetition lines (e.g. `eft eft eft …`, or embedded in a short carrier) within a per-line length budget, until the count is met. Drill lines come last and are flagged.
- Output: ordered prompt lines (natural first, drills last), each annotated with the target labels it contributes; plus a coverage report (per target: achieved count, natural vs drill, met?, and any genuinely unmet).

### `layout.py`
Pure geometry — the single source of truth. Given a page config (page pixel size + dpi, margins, prompt font size, prompt→line gap, writing-line height, row pitch) and the ordered prompt lines, paginate into rows and compute, for each row:
- `bbox` — the writing-line rectangle (left margin, computed top, usable width, line height)
- `baseline_y` — where the rule sits
- `expected_transcript` — the sentence
- `expected_units` — the ordered in-charset grapheme labels of the transcript
- `ligature_targets` — the target ligatures present in that row

Plus per page: `width_px`, `height_px`, `dpi`, and `source_bounds` (the full page rectangle — the reMarkable export is the whole page). All geometry is expressed in the **Contract X coordinate convention: top-left origin, +x right, +y down, units = pixels at the page dpi** (matching how a rasterized page image is indexed, which is what `ingest-segment` works in). Validates that rows fit the page before returning.

### `pdf.py`
ReportLab renderer. Consumes the layout model; for each row draws the faint prompt text and the writing-line rule at the computed coordinates. Owns the **single** conversion from the pixel/top-left space to ReportLab's points/bottom-left space (`pt = px/dpi*72`, `y_pt = page_height_pt − y_px/dpi*72`). Writes `capture.pdf`.

### `sidecar_out.py`
Consumes the *same* layout model; builds `hwfont_schema.CaptureSidecar` (pages → `Page(width_px, height_px, dpi, source_bounds, regions=[Region(id, expected_transcript, baseline_y, bbox, expected_units, ligature_targets)])`) and serializes it to `capture.sidecar.json`.

### `generate.py` + CLI
Orchestrates a run: load target spec → load corpus → plan → layout → render PDF + emit sidecar → write `targets.json` (the `Target` set, to seed a glyph store with matching coverage targets) → print the coverage report (met / drill-filled / unmet). Refuses to overwrite an existing output directory unless `--force`.

## Data Flow

1. `targets.py` produces the `Target` list (coverage definition).
2. `corpus.py` produces filtered candidate sentences from the configured sources.
3. `planner.py` selects/derives the ordered prompt lines and the coverage report.
4. `layout.py` paginates the lines and computes all geometry in pixel/top-left space → the layout model.
5. `pdf.py` and `sidecar_out.py` both consume the layout model → `capture.pdf` and `capture.sidecar.json`.
6. `generate.py` also writes `targets.json` and prints the coverage report.

## Error Handling

Rule: **flag, never silently emit something under-covering.**
- **Target spec:** invalid YAML/JSON, unknown `kind`, non-positive count, or duplicate labels → clear validation error.
- **Corpus:** missing/empty source dir → error; out-of-charset sentences filtered (count logged); zero usable sentences → error before planning.
- **Planner:** a target still unmet after drill-fill → reported as **unmet**; the run flags it loudly and exits non-zero rather than shipping an under-covering booklet.
- **Layout:** a config that can't fit (writing-line + prompt taller than the row pitch, margins exceeding the page) → explicit error naming the offending numbers, validated before rendering.
- **Output:** refuse to overwrite an existing output dir unless `--force`.
- **Determinism:** identical inputs produce a byte-identical sidecar and stable PDF ordering.

## Testing Strategy (TDD throughout)

- **targets:** parse/validate config; defaults; reject bad kind/count/duplicates.
- **corpus:** sentence segmentation on sample text; charset filtering drops out-of-set sentences; deterministic order; empty-source error.
- **planner:** greedy meets coverage on a small target+corpus fixture; non-overlapping ligature counting; drill-fill tops up a ligature the corpus lacks (flagged as drill); an impossible target surfaces as unmet (not dropped); identical inputs → identical ordered lines.
- **layout:** regions within page, non-overlapping, baseline inside bbox, correct px math, pagination across pages, invalid config raises; px→pt conversion helper on known values.
- **pdf:** render then reopen with `pypdf` → expected page count / valid PDF (kept light).
- **sidecar:** emitted `CaptureSidecar` validates against `hwfont_schema`; geometry equals the layout model; `expected_units`/`ligature_targets` correct; region count == prompt-line count; round-trips.
- **integration (`generate`):** tiny target spec + tiny corpus → valid `capture.pdf`, validating `capture.sidecar.json` (regions match lines), `targets.json`, coverage report all-met; re-run produces an identical sidecar.

## Technology

- **Language:** Python 3.12.
- **PDF:** ReportLab.
- **Config:** PyYAML.
- **PDF inspection (tests only):** pypdf.
- **Sentence segmentation:** lightweight rule-based splitter (no heavy NLP dependency).
- **Bundled default corpus:** a few public-domain literature excerpts + a famous English speech, stored as package data text files.
- **Dependency on `hwfont-schema`** for `Target` and Contract X models.

## Out of Scope (this spec)

- The actual writing/scanning step and reMarkable export handling (belongs to `ingest-segment`).
- Any segmentation or font logic.
- A large curated corpus — only a small bundled default set ships; users point at their own text directory for more.
- Internationalization / non-Latin scripts.

## Coordinate Convention (normative)

All Contract X geometry uses: **origin top-left, +x right, +y down, units = pixels at the page's `dpi`.** `source_bounds`, region `bbox`, and `baseline_y` are all in this space. `pdf.py` is the only component that converts to ReportLab's bottom-left/points space, and that conversion lives in one place.

# Context: reMarkable adapter review + revision (session handoff)

**Date:** 2026-11-14
**Branch:** `claude/vibrant-fermi-c3unfa` (was identical to `main` at `4c841dc` when the session started)
**State:** all work below is **uncommitted** in the working tree. Nothing has been pushed.
The cloud container is ephemeral: commit and push before it is reclaimed or the edits are lost.

## 1. What was asked

1. "code review — go to town" on the reMarkable export adapter series (15 commits since
   the design spec, `d54cd4a..4c841dc`).
2. "look at fit for use and fit for purpose and explore ideas".
3. "do it all" — fix every finding and build out every idea.
4. "do an end to end test" (mid-task): add a real end-to-end test, capture-template → export → CLI.
5. Interrupted with "save context to file" (this document).

## 2. Code review findings (14, all still open in committed code)

Correctness, most severe first:

1. `run.py` size-mismatch guard was downgraded to a note claiming "alignment will reconcile",
   but the affine was applied to strokes only; region crops and candidate bboxes indexed the
   un-warped raster with sidecar coordinates. Production sidecar is 1404x1872, real export
   viewBox is 1410x1880, so every real run hit this.
2. `centerline()` zero-area test used signed shoelace area: self-intersecting rings (figure-8,
   cursive loops) cancel to ~0 and were silently dropped.
3. `_parse_path_d` number regex misparsed `.5` (→5), exponents, and treated relative `l` as absolute.
4. `_parse_path_d` merged all subpaths of one `d` into one ring: holes of o/a/e/d/g/p/b/q were
   stitched to the outer ring with bridge edges.
5. `--raster` + `--svg` together used the override raster's size as `export_size` while strokes
   were in SVG viewBox space.
6. Provenance `source_raster` was the fabricated literal `"page.png"` when the raster came from the SVG.
7. Closed-loop skeleton trace never walked back to its start pixel (open contour for a closed "o").
8. 2-point rings bypassed the zero-area rule and became phantom strokes.
9. Unit-suffixed `width`/`height` ("1410px") raised ValueError when no viewBox; missing both gave a 0x0 page.
10. Every `<path>` was treated as ink, including those under `<defs>`/`<clipPath>`/`<mask>`; no `fill="none"` check.
11. Template PNG silently resized to the viewBox size (spec requires logging the mismatch).

Efficiency / tests / cleanup:

12. Skeletonization and paid vision calls ran before the `--out`/`--force` check.
13. SVG CLI test asserted only rc==0 + file exists (could not catch alignment or assignment regressions);
    no `--raster`+`--svg` test; real-export test lacked the spec's fiducial assertion.
14. `svg-raster = ["cairosvg>=2.7"]` extra was dead.

Test status at review time: 41 passed / 2 skipped (ingest-segment), 32 passed (hwfont-schema).

## 3. Fit-for-purpose assessment (summary)

- The export already hands over the real filled ink outline with pressure-varying width; the
  adapter skeletonized it to a 1-px centerline and discarded the outline, and font-gen planned to
  re-inflate with a synthetic nib. Keep outlines alongside centerlines.
- The composited raster drew 3-px uniform lines, so vision/review saw a tracing, not the ink.
- Alignment only mattered for export-frame vs sidecar-frame; it must be applied to the raster too.
- Vision pixel boxes are the weakest link; the device already segments per stroke, so vision should
  *group numbered strokes* into units.
- No per-stroke skeleton-quality signal existed (branches/loops silently lost).

Ideas adopted: distance-transform widths; outlines in StrokeData; stroke-ID grouping protocol;
native `.rm` adapter (rmscene); branch decomposition + coverage score; dry-run overlay;
synthetic export fixture; page-index from filename; early out-dir check; honest provenance;
capture-template PNG template rasters.

## 4. Work completed in the working tree (uncommitted)

### hwfont-schema (done, tests pass: 35)
- `strokes.py`: `StrokePoint.width` (ink width, same units as x/y), new `Outline` model
  (`rings`, `fill_rule` nonzero|evenodd), `StrokeData.outlines: list[Outline] | None`.
- `candidate.py`: `Candidate.review_reasons: list[str]`; `CandidateProvenance.source_raster`
  now optional, new `source_rm`.
- `__init__.py` exports `Outline`. Tests updated in `tests/test_strokes.py`, `tests/test_candidate.py`.

### ingest-segment source (written, NOT yet exercised by the updated tests)
- `remarkable_svg.py` (full rewrite):
  - Real `d` tokenizer/parser: M/L/H/V/Z/C/S/Q/T abs+rel, implicit repeats, subpaths; arcs raise.
  - `parse_svg` → `ParsedSvg(viewbox, template_png, paths: list[InkPath])`; skips defs/clipPath/mask/
    symbol/pattern/marker subtrees and `fill="none"`; inherits `fill`/`fill-rule` (attr or style);
    unit-suffixed lengths parsed; clear errors for no size / malformed viewBox.
  - `rasterize(rings, fill_rule)` → padded bool mask + origin; nonzero via signed per-ring winding
    sum (PIL polygon fill is even-odd, verified), evenodd via XOR.
  - Degeneracy = collinearity (`_is_collinear`), not signed area; `<3` points → None.
  - `_skeleton_paths`: peel longest path (double BFS), then remaining components become branches
    joined back to the traced pixel they touch; closed loops close onto their start; spurs
    `< _MIN_BRANCH_PX` dropped; returns (paths, covered-pixel count) → `coverage`.
    (A bug where the whole component was subtracted instead of the path was found and fixed.)
  - Widths from `scipy.ndimage.distance_transform_edt` (width = max(1, 2*edt-1)), kept parallel
    to RDP-kept points (`_rdp_keep` returns indices).
  - `PenStroke` dataclass: `centerlines`, `widths`, `outline`, `fill_rule`, `coverage`, `is_stub`,
    `pressures` (native only); properties `points`, `bbox`, `low_coverage` (`LOW_COVERAGE = 0.9`).
  - `draw_ink` / `compose_raster(template bytes|Image|None, strokes, page_size)` paint real outlines
    (or width-aware centerlines when no outline).
  - `RemarkableExport` gains `template_size`, `template_resized`, `low_coverage_strokes`, `summary()`.
  - Smoke probes passed: subpaths, `.5`/exponent/relative parsing, arc rejection, bowtie kept
    (coverage 1.0 with 3 polylines), collinear & 2-pt → None, nonzero hole vs same-orientation,
    evenodd, annulus closed loop coverage 1.0 width≈10, T-shape → 2 polylines coverage 1.0,
    loop+stem → 2 polylines.
- `align.py`: added `invert_affine`, `affine_scale`, `is_identity`, `transform_stroke` (scales
  widths), `warp_raster` (PIL AFFINE with inverse matrix, identity short-circuit).
- `candidates_out.py`: `_strokes_to_data(list[PenStroke])` emits Contours with width/pressure and
  `outlines`; `write_candidate_set` items are `(Candidate, list[PenStroke])`.
- `segment.py` (rewrite): `StrokeSummary`, `VisionUnit`, `VisionResult{boxes, units}`,
  `VisionFn = Callable[[bytes, Region, list[StrokeSummary]], VisionResult]`; `suggest_groups` gap
  heuristic; `build_grouping_prompt`; `ClaudeVisionClient.__call__(crop, region, strokes=())`
  picks grouping vs box protocol; `_annotated_crop_png` draws numbered colored strokes;
  `segment_region` uses grouping when the region has strokes (bbox = padded union of assigned
  ink), box protocol otherwise; review reasons: unknown_stroke_id, no_strokes, stroke_shared,
  unassigned_stroke_nearby, count_mismatch, page_low_confidence, low_confidence, low_coverage_stroke.
- `run.py` (rewrite): `PreparedPage` + `prepare_page` (fiducials → affine → warp raster AND
  strokes into page space); `ingest_page(page, raster, strokes, vision, model, created_at, out_dir,
  source_raster=None, source_svg=None, source_rm=None, force=False, debug_dir=None)` — the
  `export_size` parameter is gone; `write_debug_overlay`; `page_index_from_name`; CLI flags
  `--svg/--rm/--template/--raster`, `--page-index` (default from filename), `--debug-dir`,
  `--dry-run`; early out-dir check; `--raster`+export size mismatch is an error; summary prints
  review reasons.
- `debug.py` (new): `render_overlay(raster, strokes, regions, candidates, fiducials, measured)`.
- `remarkable_rm.py` (new): `iter_lines`, `strokes_from_lines(lines, offset)` (x + page_w/2,
  pressure/255, width as-is), `load_rm_export(rm_path, page_size, template=None)`; needs
  `rmscene` (0.8.0 installed in the venv; API: `read_blocks`, `SceneLineItemBlock.item.value.points`
  with fields x, y, speed, direction, width, pressure).
- `__init__.py`: exports updated (PenStroke, prepare_page, transform_stroke, warp_raster,
  render_overlay, load_rm_export, StrokeSummary, VisionUnit).
- `pyproject.toml`: `pillow>=10.1`, `scipy>=1.11`; extras `dev = [pytest, rmscene, capture-template]`,
  `rm = [rmscene]`; cairosvg extra removed.

### capture-template (written, untested)
- `raster.py` (new): `render_page_raster(page, prompt_font_px, prompt_gap_px, fiducial_radius_px,
  prompts=True)` draws rules/prompts/fiducials from a sidecar `Page`; `render_model_rasters(model,
  out_dir)` writes `page-<n>.png`.
- `generate.py`: `generate(..., png=False)` and `--png` flag write those rasters.

### Test scaffolding (written)
- `packages/ingest-segment/tests/remarkable_fixture.py` (new): `sweep`, `arc`, `annulus`,
  `Glyph`, `make_glyph` (l, i, t, o, c, a, h, default wavy), `layout_text`, `write_export(path,
  page_size, strokes, template, pad, ink_offset, extra, fill_rule)` producing a centered-viewBox
  export with the template pasted at `ink_offset` into a `page_size + pad` canvas.

## 5. Remaining work (in order)

1. Rewrite/update ingest-segment tests to the new APIs (old tests will fail to import):
   - `test_remarkable_svg.py` (parse_svg returns `ParsedSvg`; `centerline(InkPath)`; `_skeleton_paths`
     replaces `_trace`; cover: subpath holes, evenodd, bowtie kept, collinear/2-pt dropped,
     defs/clipPath skipped, fill=none skipped, `.5`/relative/curve parsing, unit lengths, no-size
     error, closed-loop closure, branch decomposition + coverage, widths, template_resized,
     outline compositing).
   - `test_segment_region.py` (both protocols, every review reason, annotated crop is RGB PNG).
   - `test_candidates_out.py` (PenStroke items; widths/outlines in strokes JSON).
   - `test_run.py` (no `export_size`; warp test: export shifted by (3,4) → crops land right).
   - `test_cli.py` (svg-only run asserting `alignment_method == "fiducial"` + strokes_path;
     `--dry-run` writes overlay and never builds the vision client; `--raster`+`--svg` mismatch → 1;
     existing out dir without `--force` → 1 before any work; page index from "page 2" filename).
   - `test_end_to_end.py`: capture-template `build_layout` + `build_sidecar` → `render_page_raster`
     template → `layout_text` glyphs for the region transcript → `write_export` with `pad=(6, 8)`,
     `ink_offset=(3, 4)` → `main([...])` with a mock grouping vision (assign each stroke summary to
     the nearest glyph center) → assert fiducial alignment, residual < 1.5 px, one candidate per
     unit, all pending, strokes JSON has outlines + widths in [2, 6], 'o' candidate bbox center x
     within ~2 px of its designed page x (proves the raster warp).
   - `test_align.py`: add `transform_stroke` / `warp_raster` / `invert_affine` tests.
   - `test_remarkable_real.py`: new API + `detect_fiducials(exp.page_raster, ...)` finds ≥3 marks.
   - `test_integration_vision.py`: call `client(png, region, [])`; optionally a grouping call.
   - new `test_remarkable_rm.py`: `strokes_from_lines` with `SimpleNamespace` points; `load_rm_export`
     via monkeypatched `iter_lines`; import error message without rmscene (optional).
   - new `test_debug.py`: overlay size/mode.
2. capture-template: `tests/test_raster.py` (fiducial pixels dark, rule row gray, size) and a
   `generate(..., png=True)` assertion in `test_generate.py`.
3. Run all three suites; fix fallout. Then `ReportFindings` again with per-finding `outcome`
   (the review tool requires it after fixes land).
4. Docs: short spec note under `docs/superpowers/specs/` describing the revised adapter contract
   (PenStroke, grouping protocol, `.rm` path, dry-run); update README usage.
5. Commit in logical chunks (schema; adapter; pipeline; rm adapter; capture-template png; tests;
   docs) and push with `git push -u origin claude/vibrant-fermi-c3unfa`. Commit messages end with
   the attribution lines given in the session reminder.

## 6. How to run

```bash
V=/tmp/claude-0/-home-user-handwriting-font/7763836d-3952-5d4d-a06e-cab75dea72a8/scratchpad/venv
# fresh machine: python3 -m venv "$V" && "$V/bin/pip" install -e packages/hwfont-schema -e "packages/capture-template[dev]" -e "packages/ingest-segment[dev]"
cd packages/hwfont-schema && "$V/bin/python" -m pytest -q
cd ../capture-template && "$V/bin/python" -m pytest -q
cd ../ingest-segment && "$V/bin/python" -m pytest -q
```

Dry run on a real export (no API spend):

```bash
ingest-segment --svg "capture_pdf - page 1.svg" --sidecar hcpdf/capture.sidecar.json --out out/p1 --dry-run
```

## 7. Decisions worth keeping

- Outline is ground truth; centerline + width is derived. Both are stored on candidates.
- Nonzero fill is computed as a signed winding sum of per-ring even-odd masks (rings assumed simple).
- Coverage = traced skeleton pixels / all skeleton pixels; `< 0.9` flags `low_coverage_stroke`.
- `ingest_page` takes raster + strokes in ONE export space and warps both into sidecar page space.
  Combining a user raster with an export of a different size is refused.
- Vision protocol is stroke grouping whenever a region has strokes; pixel boxes only for raster-only pages.
- `.rm` adapter maps device x by +page_w/2; fiducials (from a `--template` PNG) reconcile scale.

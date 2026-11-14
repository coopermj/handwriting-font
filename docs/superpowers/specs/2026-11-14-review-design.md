# review — Module Design

**Date:** 2026-11-14
**Status:** Approved for planning (owner's standing instruction: proceed without questions, decide and record rulings)
**Module:** `review` (architecture Module 3, the human-in-the-loop step)
**Depends on:** `hwfont-schema` (Contracts X, Y, the `Candidate` contract, em-v1 in `hwfont_schema.emspace`), Pillow, numpy; FastAPI + uvicorn for the UI only
**Parent spec:** [architecture](2026-06-15-handwriting-font-architecture-design.md)
**Upstream:** `ingest-segment` (emits the `CandidateSet` this module consumes)
**Downstream:** `font-gen` (reads the `GlyphStore` this module writes; its em-v1 convention, [font-gen spec §3](2026-11-13-font-gen-design.md), is normative here)

## Purpose

`review` is the missing link between `ingest-segment`'s candidates and `font-gen`'s glyph store. A `CandidateSet` holds proposed glyph boxes in page pixels with a label, a confidence and the ink inside the box. `review` lets a human (or a script) confirm, correct or discard each candidate and, on accept, normalises the candidate's ink to the em-v1 frame that `font-gen` requires and writes it as a Contract Y `Sample` with its stroke and raster sidecars. It keeps a live coverage report against the store's targets.

The module is **headless first**: every review operation is a pure function on an in-memory review state, every em-v1 computation is a pure function of a candidate plus session statistics, and disk I/O is confined to three places (loading the inputs, saving the decision log, writing the store). The browser UI is a thin FastAPI layer over exactly those functions.

## Design Decisions (rulings)

1. **Write-through.** Accepting a candidate writes its `Sample` and sidecars to the store immediately; rejecting an accepted candidate removes them. The store is always the exact image of the decision log, so the coverage panel reads the store and `font-gen build --store` can be run at any moment. (Alternative considered: batch "commit" at the end — rejected because it defers the one thing the owner wants to see, coverage, and because it needs a second reconciliation path.)
2. **Decisions live beside the candidates, never in them.** `review.json` in each `CandidateSet` directory records one `Decision` per candidate of that directory (status, current label and kind, current box, quality, flags, note, `updated_at`). `candidates.json` is never rewritten. A session with several pages holds one log per directory. The log's `session_id` must equal the session's (`--session`, default the sidecar stem); a log written under another id is a `ReviewError` naming both ids, never a silent re-prefixing of sample ids. A sample's `created_at` is the decision's `updated_at`, so replaying the logs into a fresh store reproduces the store exactly (`font_gen.load.store_digest` equal).
3. **Session statistics and neighbour geometry are fixed at load.** The session x-height, the median half-gap, the unit order within a region and every neighbour's ink extent are computed once from the *original* candidates (their vision boxes, labels and assigned strokes), over every candidate with ink regardless of its review status. A review operation therefore changes exactly one sample — its own — and `replay` reproduces write-through bit for bit; every sample in a session carries the same `x_height` and the same missing-neighbour sidebearing, whichever order the reviewer works in. Medians are robust to the occasional mis-segmented candidate. (Alternative: recompute neighbours from current boxes and re-mirror up to three samples per operation — rejected because it makes the store depend on operation order and ties `unit_order` to the log; alternative: recompute statistics from accepted candidates at each write — rejected because early and late samples would disagree and `font-gen` would flag `x_height_outlier` on its own input.) Consequence: a box adjustment changes which strokes the candidate owns and its own extent, but its neighbours still see the candidate's original ink; acceptable because the original strokes are physical ink wherever they were boxed.
4. **A neighbour's ink is physical, but a neighbour must be the unit the transcript names.** Half-gap sidebearings are measured against the adjacent unit's ink in the region whether or not that unit has been accepted, rejected or relabelled; rejection means "do not make a glyph of this", not "this ink is not on the page". The adjacent unit counts as the neighbour only when its original label agrees with the candidate's `Context` (`adjacent.label[-1] == left_neighbor` on the left, `adjacent.label[0] == right_neighbor` on the right): ingest derives `Context` from the box index, so a missed letter, an extra box or a ligature box shifts every later `Context` by one, and measuring against the wrong unit would yield a confident, wrong advance. The `Context` used here and stored on the sample is the **effective context**: re-derived from the transcript by cumulative character offsets (a ligature box covers `len(label)` units) when the region's original labels in unit order spell the transcript with whitespace removed, else the candidate's (amendment C1). A mismatch, a missing neighbour (`None` in `Context`, a word edge) or a neighbour without ink falls back to the session median on that side; a mismatch is flagged `neighbor_mismatch` on the sample. Only matched pairs feed the median half-gap.
5. **Page raster is optional; the crop is the fallback.** When the page raster is supplied, raster sidecars are resampled from it (real ink). Without it, raster-only candidates use their ingest crop (padded with white where the padded em box exceeds the crop) and stroke-bearing candidates get no raster sidecar at all (`font-gen` never needs the raster when strokes exist). `review` never re-renders a raster from centerlines: a centerline re-render is not ink.
6. **Sample ids are `<session>-<candidate id>`** (`sess1-p0-r3-7`), where the session id is the `--session` argument (default: the sidecar file's stem). Candidate ids are unique within a sidecar; the session prefix keeps two capture sessions apart in one store.
7. **Stroke reassignment on box adjustment is confined to the region's stroke pool**: the union of the region's candidates' strokes, deduplicated. Ingest keeps only the strokes it assigned to some box, so unassigned ink is not recoverable here (noted as an upstream follow-up: emit the page's full stroke list in the `CandidateSet`).
8. **Merging or splitting candidates is deferred.** The operations are `accept`, `reject`, `relabel`, `adjust_box` and `set_quality`; a vision pass that boxed `t` and `h` separately where the writer joined them is handled today by accepting both singles. A `merge` operation layers on the same decision log later.
9. **Targets are seeded from the sidecar** when the store has none: every character in every region's `expected_units` becomes a `single` target (required 12) and every `ligature_targets` entry a `ligature` target (required 8). `--targets targets.json` (a JSON list of `Target`) overrides. `review` does not import `capture-template` (modules depend only on `hwfont-schema`).
10. **em-v1 violations downgrade, never block.** A materialised sample that violates an em-v1 invariant is still written, with `quality` downgraded to `marginal` and the violations recorded in the decision's `flags`; the UI shows them. Only a pydantic validation failure (a contract violation) refuses the write and raises `ReviewError`. The advance clamp (`≤ 0` → `0.02`, marginal) is as em-v1 specifies.
11. **The UI is a single static page** (vanilla JS, one `<canvas>`, no build step) served by FastAPI from the package; the server composites the page image (raster, or the crops placed on a white page when no raster was supplied) so the client draws the same thing in both cases.

## Approaches considered

- **A. Pure functions over an explicit `ReviewState` + write-through store (chosen).** Operations return new state; a thin `apply()` persists the log and mirrors the store. Testable headless, replayable, and the UI needs nothing but the same calls.
- **B. Stateful `ReviewSession` object with mutating methods and a final `commit()`.** Simpler to write, but the coverage panel would have to be computed from two sources (store + uncommitted), and a crash mid-session loses work. Rejected.
- **C. Review directly in the store** (write every candidate as a `pending` sample, then flip `review_status`).** Uses the enum's `pending`/`needs_review` values, but every page's junk would sit in the store, `GlyphStore` has no update path, and `font-gen`'s digest would churn with every click. Rejected; the `pending` status stays available for a future bulk import.

## Architecture

New package `packages/review/` (hatchling, `src/` layout, mirrors `ingest-segment`):

```
review/
  __init__.py     re-exports, __version__
  errors.py       ReviewError
  inputs.py       load sidecar + CandidateSet dirs (+ optional page rasters) → ReviewInputs (frozen)
  decisions.py    Decision / ReviewLog models; pure ops accept/reject/relabel/adjust_box/set_quality; load/save review.json
  ink.py          ink extents (strokes or crop pixels), region stroke pool, stroke reassignment, raster resampling
  session.py      SessionStats (x-height, median half-gap) and per-region unit ordering/neighbour lookup
  normalize.py    materialise(candidate, decision, inputs, stats) → Materialised(sample, strokes, raster, flags)
  store_sync.py   write-through: mirror a decision into the GlyphStore (add/remove), seed targets, replay
  coverage.py     CoverageReport from store + log
  run.py          CLI: review init | apply | replay | coverage | serve
  app.py          FastAPI app factory over the same functions
  static/index.html, static/app.js
```

**Data flow:**

```
capture.sidecar.json (X) + <candidates dir>[…] (CandidateSet) [+ page PNG…]
  → inputs.load → ReviewInputs(pages: {page_id: PageInputs(page, candidate_set, strokes, crops, raster?)})
  → session.compute_stats → SessionStats(x_height, median_half_gap, flags)
  → decisions.load_log → ReviewLog (one Decision per candidate; defaults from the candidate)
  → op (pure) → new ReviewLog
  → store_sync.apply(store, inputs, stats, old_log, new_log) → writes/removes the affected samples, saves review.json
  → coverage.report(store, log)
```

## Inputs (`inputs.py`)

- `load_inputs(sidecar_path, candidate_dirs, rasters={page_id: path}, session_id)` reads the sidecar (Contract X), each `candidates.json` (via `CandidateSet.model_validate_json`; `review` reads the manifest format `ingest_segment.candidates_out` writes, but does not import `ingest-segment`), every candidate's `strokes/<id>.json` and `crop/<id>.png`, and any page raster.
- Each `CandidateSet` is matched to the sidecar page by `provenance.source_page_id == Page.id`; a missing page is a `ReviewError`. Two sets for the same page is a `ReviewError`.
- A page raster whose size differs from `(Page.width_px, Page.height_px)` is a `ReviewError` (ingest already reconciled the export to page pixels; review does no alignment).
- `ReviewInputs` and `PageInputs` are frozen dataclasses; strokes are `StrokeData` in page pixels (y down), crops are PNG bytes.

## Decisions (`decisions.py`)

```python
class Decision(BaseModel):
    candidate_id: str
    status: DecisionStatus            # pending | accepted | rejected
    label: str                        # current label (starts as the candidate's)
    kind: Kind                        # follows the label-length rule
    bbox: BBox                        # current box, page px
    quality: Quality = good
    flags: list[str] = []             # em-v1 violations / notes from the last materialisation
    note: str = ""
    updated_at: str                   # ISO-8601, caller-supplied
class ReviewLog(BaseModel):
    version: str = "1"
    session_id: str
    decisions: dict[str, Decision]    # keyed by candidate id
```

Pure operations (`ReviewLog → ReviewLog`, never mutate; unknown id → `ReviewError`):

- `accept(log, id, now, quality=None)` — status `accepted`, quality from the argument or the decision's current quality.
- `reject(log, id, now)` — status `rejected`.
- `relabel(log, id, label, now)` — `label` must be non-empty with no whitespace; `kind` follows the length rule. Context is kept (the word and neighbours come from the transcript, not from the label). A label that does not occur in the region's `expected_units`/`ligature_targets` gets flag `label_not_expected`. An accepted candidate stays accepted (the store is re-mirrored).
- `adjust_box(log, id, bbox, now)` — the box must have positive size, lie within the page (`0 ≤ x`, `x + w ≤ width_px`, likewise y) and intersect its region's bbox; otherwise `ReviewError`. (Ruling 2026-11-14: "fully inside the region" was withdrawn — descenders and nudged boxes legitimately extend into the inter-row gap, and ingest's own boxes may overhang the ruled row.) An accepted candidate stays accepted (re-mirrored).
- `set_quality(log, id, quality, now)`.
- `reset(log, id, now)` — back to the candidate's original label/box, status `pending`.

`default_log(inputs)` builds a `ReviewLog` with a pending `Decision` per candidate; `load_log(dir, inputs)` reads `review.json` and fills in any candidate the log does not know (new ids) with defaults; a decision for an id not in the set is a `ReviewError`. `save_log` writes atomically (`.tmp` + `os.replace`).

## Ink (`ink.py`)

- `stroke_extent(strokes_px) → Extent(xmin, xmax, ymin, ymax)` over all points.
- `crop_extent(crop_png, crop_origin) → Extent | None` — dark pixels (gray `< INK_THRESHOLD = 128` after converting to `L`) → pixel bbox, returned in page px (`xmin = ox + col_min`, `xmax = ox + col_max + 1`), `None` when the crop has no ink. `crop_origin = (int(bbox.x), int(bbox.y))` exactly as `ingest_segment.segment._crop_png` truncates.
- `candidate_extent(candidate, strokes, crop) → Extent | None`: strokes when present, else crop ink.
- `region_pool(page_inputs, region_id) → list[list[(x, y)]]`: every stroke of every candidate in the region, deduplicated by point sequence.
- `assign_strokes(pool, bbox) → list[stroke]`: the strokes with `≥ INSIDE_FRACTION = 0.5` of their points inside the box (the ingest rule, kept so an unadjusted box reproduces the ingest assignment exactly).
- `resample_raster(source_img, source_origin, bbox_em, origin_px, scale_px_per_em, s) → PNG bytes`: the em box `bbox_em` padded by `RASTER_PAD` on every side, mapped back to page px, resampled (bicubic) to `round((bbox.w + 2·PAD)·RASTER_SCALE) × round((bbox.h + 2·PAD)·RASTER_SCALE)` pixels with `RASTER_SCALE = 256` px/em, white outside the source image. `source_img` is the page raster or the crop (with its origin).

## Session statistics (`session.py`)

- `unit_order(page_inputs, region_id) → list[candidate_id]`: the region's candidates sorted by the centre x of their *original* box, ties by id. `neighbours(page_inputs, region_id, candidate_id) → (left_id | None, right_id | None)`: the adjacent entries in this order, each only when the candidate's effective context (`effective_context(page_inputs, region_id, candidate_id)`, ruling 4) names a neighbour on that side **and** the adjacent unit's original label agrees with it (ruling 4); a word edge has no neighbour even if another unit sits next to it. Both depend on `inputs` alone, never on the log.
- `compute_stats(inputs) → SessionStats(x_height, median_half_gap, n_x_height_letters, n_gaps, flags)`:
  - **x-height**: for every candidate whose current label is one of `XH_LETTERS = "acemnorsuvwxz"` and that has ink, the 90th percentile (numpy `percentile(…, 90)`) of its stroke points' em y (y-up, baseline from its region), or the ink top for a raster-only candidate; `x_height` = median over candidates. No such candidate → `X_HEIGHT_DEFAULT = 0.45` and flag `x_height_assumed`.
  - **median half-gap**: over every measurable neighbour pair (both units have ink, `Context` says they are neighbours), `½·(right.xmin − left.xmax)·s` in em; median; none → `HALF_GAP_DEFAULT = 0.05` and flag `half_gap_assumed`.
  - Scale `s = 1 / region.bbox.h` per region (one constant per template layout; regions of one page share it).
  - Stats are computed from the *original* candidate boxes, labels and strokes (ruling 3).

## Normalisation (`normalize.py`) — em-v1, exactly as `hwfont_schema.emspace`

`materialise(inputs, stats, log, candidate_id) → Materialised(sample, strokes_em | None, raster_png | None, flags)`; pure; raises `ReviewError` when the candidate has no ink. `created_at = decision.updated_at` (ruling 2).

1. Region `R` = the candidate's region; `s = 1 / R.bbox.h`; `baseline_px = R.baseline_y`.
2. Strokes: if the decision's box equals the candidate's original box, the candidate's own strokes; otherwise `assign_strokes(region_pool, box)`. Empty → raster-only if the crop/page raster has ink inside the box, else `ReviewError("no ink")`.
3. `ink = Extent` of the centerline points (or of the ink pixels inside the box for raster-only).
4. Half-gaps: `LSB_px = ½·(ink.xmin − left.xmax)` when `neighbours()` yields a left neighbour and it has ink (its *original* extent), else `stats.median_half_gap / s`; `RSB_px` likewise. Negative values kept. A `Context` neighbour that `neighbours()` rejected for a label mismatch adds the flag `neighbor_mismatch` (quality unchanged).
5. `origin_x_px = ink.xmin − LSB_px`. Every point: `x_em = (x_px − origin_x_px)·s`, `y_em = (baseline_px − y_px)·s`; `pressure` passed through; `width`, when present, scaled by `s`.
6. `bbox_em` = tight box of the transformed points (so `check_sample_em`'s bbox test passes by construction); raster-only: the ink-pixel box transformed the same way. A zero-extent axis (a perfectly flat or vertical 2-point stub, which em-v1 allows for a dot) is widened to `STUB_EXTENT = 0.002` em on that axis (inside em-v1's `BBOX_TOL = 0.005`, so the stored bbox still passes) and the sample is flagged `stub_extent`; quality unchanged (ruling 2026-11-14).
7. `advance = bbox.x + bbox.w + RSB_px·s`; if `≤ 0`: `advance = ADVANCE_CLAMP = 0.02`, quality `marginal`, flag `advance_clamped`.
8. `Metrics(baseline=0.0, x_height=stats.x_height, advance, bbox)`; Context is the effective context: re-derived from the transcript when the row's labels spell it, else the candidate's; `kind` from the decision; `review_status=accepted`; `quality` from the decision (downgraded per 7/10); `capture_session_id = log.session_id`; `id = f"{session_id}-{candidate.id}"`; `created_at = decision.updated_at`.
9. Raster: page raster if present, else the crop; resampled per `ink.resample_raster` at `RASTER_SCALE`; for a stroke-bearing candidate without a page raster, `None`.
10. `check_sample_em(sample, strokes_em, raster)`: violations → `flags`, quality `marginal` if it was `good`.

Nothing is smoothed, resampled or rotated; contours and point order are preserved (em-v1 forbids per-sample normalisation; `font-gen` smooths).

## Store synchronisation (`store_sync.py`)

- `seed_targets(store, inputs, targets=None)`: if `store.targets()` is empty, add the given targets or those derived from the sidecar (ruling 9).
- `mirror(store, inputs, stats, log, candidate_id) → Decision`: the one place that writes. If the decision is `accepted`: materialise, `store.remove_sample(id)` if present, `store.add_sample(...)`, return the decision with `flags`/`quality` updated from materialisation. Else: `remove_sample` if present.
- `apply(op, …)` convenience: `new_log = op(log, …)`; `mirror` for the changed id; `save_log`. Used by the CLI and the app.
- `replay(store, inputs, stats, log)`: mirror every decision of one log (used by `review replay` and by tests); a session replays each page's log in turn.

**Precursor in `hwfont-schema`:** `GlyphStore.remove_sample(sample_id) -> bool` (deletes the row and both sidecars; `False` when absent) and `GlyphStore.get_sample(sample_id) -> Sample | None`. Both tested in `tests/test_store.py`.

## Coverage (`coverage.py`)

`report(store, log) → CoverageReport(rows: list[CoverageLine], summary)` where a line is `label, kind, required, accepted, pending, met` (`pending` = decisions still `pending` with that label in the loaded pages); `format_report()` renders `a: 14/12 ✓ (+3 pending)` lines, unmet first, the format the architecture spec sketches. Labels in the log with no target are listed under "untargeted".

## CLI (`run.py`, console script `review`)

```
review init     --store S --sidecar X [--targets T.json]                      # create the store + seed targets
review apply    --store S --sidecar X --candidates DIR [--candidates DIR…] [--raster PAGE=PNG…] [--session ID]
                (--accept ID [--quality q] | --reject ID | --relabel ID=LABEL | --box ID=x,y,w,h | --reset ID)…
review replay   --store S --sidecar X --candidates DIR… [--raster …]           # rebuild the store's samples from review.json
review coverage --store S --sidecar X --candidates DIR…                        # print the report
review serve    --store S --sidecar X --candidates DIR… [--raster …] [--host 127.0.0.1] [--port 8765]
```

`--now` (ISO-8601; default: now, UTC, seconds) is the `updated_at` stamped on every decision an invocation changes, and through it the sample's `created_at`. Exit 1 with a one-line message on `ReviewError`/`OSError`.

## UI (`app.py`, `static/`)

FastAPI app factory `create_app(session) → FastAPI` holding the open session (store, inputs, stats, one in-memory `ReviewLog` per candidate directory, each persisted on every change); `updated_at` for a change is the server's current UTC time. Endpoints:

- `GET /` static page; `GET /api/session` → pages, regions (bbox, baseline, transcript), candidates with decisions and stats; `GET /api/page/{page_id}/image.png` → the composited page (raster or crops on white); `GET /api/candidate/{id}/crop.png`.
- `POST /api/candidate/{id}/accept {quality?}`, `/reject`, `/relabel {label}`, `/box {x,y,w,h}`, `/quality {quality}`, `/reset` → the updated decision (with flags). `GET /api/coverage` → the report.
- Errors: `ReviewError` → 400 with the message.

Canvas page: the page image with every candidate's box drawn in its status colour (pending grey, accepted green, rejected red, needs-review candidates dashed), the current candidate highlighted; the side panel shows the crop, label field, quality radios, flags, context; keys `a` accept, `r` reject, `j`/`k` next/previous (lowest confidence first, the ingest order), `e` edit label, `g`/`m`/`b` quality; box handles draggable on the canvas; a coverage panel (unmet first) refreshed after every change. No framework, no bundler; `uvicorn` only in `serve`.

## Error Handling

Rule: flag, never silently drop.

- Inputs: missing sidecar/manifest/sidecar page/stroke file → `ReviewError` at load (fail fast; a candidate with a `strokes_path` whose file is missing is an input error, not a silent raster fallback). Missing crop with no strokes → the candidate loads but accept raises `ReviewError("no ink")`.
- Decision log for a candidate id not in the set → `ReviewError`; candidates unknown to the log → default decisions (the set grew); log `session_id` ≠ the session's → `ReviewError`.
- Materialisation: em-v1 violations → marginal + flags (ruling 10); schema failure → `ReviewError`, nothing written, log unchanged.
- Store writes are per-sample atomic (`GlyphStore.add_sample` already rolls back); `review.json` is saved after the store mirror so a crash between the two leaves the store ahead of the log, which `replay` reconciles (it re-mirrors every decision).

## Testing Strategy (TDD; plain pytest; synthetic fixtures only)

- **`hwfont-schema`:** `remove_sample` removes row + sidecars and returns `False` on absence; `get_sample`.
- **`tests/synth_page.py`** (test helper, not package code): builds a synthetic page — sidecar with `N` regions of height `70` px and transcripts from a list, polyline ink for each unit from a tiny parametric alphabet placed on the baseline in page px, a page raster drawn from those polylines, and a `CandidateSet` directory written in the ingest manifest format (strokes per candidate, crops, confidences, contexts via a local copy of the transcript-unit rule). Deterministic from a seed.
- **`inputs`:** loads a synthetic set; page mismatch, duplicate page, raster-size mismatch → `ReviewError`.
- **`decisions`:** every op is pure (input log unchanged), relabel kind rule, box-outside-region refusal, `label_not_expected`, log round trip, unknown-id handling, atomic save.
- **`ink`:** stroke/crop extents (crop origin truncation pinned), pool dedupe, assignment reproduces the ingest assignment for the original box, resample size/scale and white padding.
- **`session`:** unit order follows original boxes (ties by id); neighbour only when `Context` says so and the adjacent label agrees (a shifted `Context` yields no neighbour); x-height = median of 90th-percentile tops (hand-checked numbers); defaults and flags when no data.
- **`normalize`:** y-flip, scale from `R.bbox.h`, half-gap LSB/RSB including a negative gap, missing-neighbour median, `neighbor_mismatch` median fallback, advance clamp → marginal, `created_at == updated_at`, bbox equals point extents, `check_sample_em` returns `[]` for ordinary synthetic ink, raster scales agree within 2 % and `≥ 32` px/em, pressure/width pass-through, raster-only path.
- **`store_sync`:** accept writes, reject removes, relabel/adjust re-mirror (old id removed, no orphan sidecars), a neighbour's adjustment does not touch an accepted sample, replay from a log equals write-through (same `font_gen.load.store_digest`).
- **`coverage`:** counts and formatting.
- **`run`:** CLI round trip on the synthetic set; `init` seeds targets from the sidecar; `--targets` override.
- **`app`:** FastAPI `TestClient` (httpx): session JSON, accept → store row appears, bad label → 400, page image is a PNG of page size.
- **End to end (`test_end_to_end.py`):** synthetic `CandidateSet` (≥ 3 regions, a transcript giving ≥ 2 samples of several letters and a ligature) → accept all pending candidates headlessly → `check_sample_em` returns `[]` for every stored sample → `font_gen.run.run_build(store, BuildOptions())` returns font bytes with `report.has_errors is False` → HarfBuzz shapes a word from the transcript to real glyphs (no `.notdef`). `font-gen` is a **dev** dependency of `review` only.

## Technology

- Python 3.12+ (repo venv 3.14), pydantic v2 (`hwfont-schema`), Pillow (crops, resampling, page compositing), numpy (percentiles, ink thresholds).
- UI: FastAPI 0.16x + uvicorn (extra `ui`), vanilla JS canvas; `httpx` in dev for `TestClient`.
- pytest.

## Out of Scope (deferred)

- Merge/split of candidates; stroke-level reassignment by clicking individual strokes.
- Recovering ink that ingest never assigned to any box (needs `ingest-segment` to emit the page stroke list).
- Multi-user / remote serving, authentication (the app binds to localhost).
- Calling `font-gen` from the UI (the owner runs `font-gen build --store` on the live store).
- Alignment nudges (ingest's fiducial affine is final; a page that needs re-alignment goes back through ingest).

## Known Risks

1. **No real `CandidateSet` has been seen.** The crop-origin truncation and the stroke-assignment rule are copied from `ingest_segment.segment` and pinned by tests; when a real export flows, those two functions are the first to check.
2. **Session x-height from vision labels.** A page where vision mislabels many x-height letters skews the median; the stat is reported with its sample count and `font-gen` flags outliers, so the failure is visible, not silent.
3. **Raster upsampling.** 70 px/em page ink resampled to 256 px/em is soft-edged; `font-gen`'s autotrace thresholds it. Only raster-only samples (the scanned fallback path) are affected.

## Amendments (2026-11-14, final review)

- **C1 — effective context (ruling 4, Normalisation step 8, `session.py`).** Context is the effective context: re-derived from the transcript when the row's labels spell it, else the candidate's. `session.effective_context(page, region_id, candidate_id)` concatenates the region's ORIGINAL labels in `unit_order`; when that equals `expected_transcript` with whitespace removed, the Context is derived locally (not imported from `ingest_segment`) at the candidate's cumulative character offset, spanning `len(label)` units (right neighbour after the label's last character, position initial/final/isolated/medial for the span). `neighbours`, `context_mismatch` (hence the median half-gap and `neighbor_mismatch`) and `materialise` use it; `/api/session` shows it. Upstream, `ingest_segment.segment.segment_region` now passes the cumulative offset and the label length to `derive_context(transcript, label, unit_index, length=1)`.

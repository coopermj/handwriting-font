# font-gen — Module Design

**Date:** 2026-11-13
**Status:** Approved for planning
**Module:** `font-gen` (README Component 2 / architecture Module 4)
**Depends on:** `hwfont-schema` (Contract Y; this spec adds one read helper, one optional field and the em-space convention module), `fonttools>=4.68`, `skia-pathops>=0.9`, `numpy>=1.26`, `pillow>=10`, `uharfbuzz>=0.40` (hard dependency: the post-build self-check and the proof sheet shape with it); optional `scikit-image>=0.22` (raster fallback), `cairosvg>=2.7` (PNG proof)
**Parent spec:** [architecture](2026-06-15-handwriting-font-architecture-design.md)
**Upstream:** `review` (does not exist yet; it will implement the em-space convention defined in §3 of this document)
**Downstream:** none — the `.otf` is the product

Everything in this document that depends on library behaviour was verified in the repo venv (`.venv/bin/python`: fontTools 4.68.0, skia-pathops 0.9.2, uharfbuzz 0.57.0, numpy 2.5.4, scikit-image 0.26.0, Pillow 13.0.0): stub fonts were compiled from the feature skeleton in §9.8 with `FontBuilder` + feaLib and shaped with HarfBuzz; the shaping results in §9.9 are actual output, including the per-ligature-lookup fix for same-tier shadowing (`office`, `affix`, `asthma`, `Bingham`, `angha`). Also verified: a chain rule that references an *empty* named lookup aborts feaLib with `FeatureLibError: 'NoneType' object has no attribute 'lookup_index'` (an unreferenced empty lookup compiles); `hb.shape(font, buf, {"liga": False})` still forms `f_i` when the same lookups are registered under `clig`; skia-pathops 0.9.2's `Path.conicTo` stores the wrong control point whenever `x1 == x2`, so font-gen never calls it (dots are stroked, §7.2); a 2-point stub through `stroke(45, ROUND_CAP, ROUND_JOIN, 4.0)` yields one contour of area 1590.75 against πr² = 1590.43; `FontBuilder(isTTF=True)` + `setupPost()` yields `post` format 2.0; `fontTools.misc.timeTools.timestampSinceEpoch` converts Unix seconds to `head` seconds; `T2CharStringPen.getCharString()` always writes an explicit width; `fontTools.qu2cu` / `Qu2CuPen` exist in 4.68; `pathops.Path` has `stroke(width, cap, join, miter_limit)`, `convertConicsToQuads()`, and `union()` takes an output pen. `potracer` and `pyclipper` are not importable in the venv and are not used.

## Purpose

Turn a reviewed glyph store (Contract Y) into an `.otf` (and optionally `.ttf`) that reproduces the writer's hand. `font-gen` is a pure function of the store: `GlyphStore → font + QA report + proof sheet`. It never knows pages, pixels, the reMarkable, or the review tool.

The owner's goal is "glorious fonts based on my handwriting and all its nuances and letter combinations. And glyph variation." Concretely the font must:

1. **Reproduce stroke nuance.** Every accepted sample becomes a smooth cubic outline of a round pen moving along the captured centerline with the captured (or modelled) width. No polygon facets, no blobs, no "cleaned-up" shapes.
2. **Use every captured letter combination automatically.** Every ligature/cluster in the store (`fi`, `ffi`, `eft`, `fore`, `ough`, `tion`, `ing`, `st`, `th`, `Th`, `Str`, `Sch`, …) fires in normal typing under default-on features, and only in the word positions it was captured in.
3. **Never look stamped.** Up to 7 captured variants per glyph, rotated by a counter that survives spaces and punctuation, so repeated letters *and repeated words* render differently. No synthetic jitter.
4. **Select positional forms** (initial/medial/final/isolated) for singles and clusters whenever the store has enough samples for them.
5. **Flag, never drop.** Every target with zero accepted samples, every degenerate outline, every suspicious metric, every fallback and padding is a line in the QA report; the built binary is reloaded and shaped before it is declared done; the build never emits a quietly broken font and never touches a file it did not create.

## Design Decisions (settled)

1. **The OpenType layer is the product.** Ligatures live in `liga` + `clig`, positional forms and cycling in `calt`, manual access in `aalt`. All are default-on in HarfBuzz, CoreText and DirectWrite consumers. Never `dlig`, `rlig`, `ss##` or `rand`.
2. **Cycling is a prime ring of 7 states carried by glyph identity**, with transparent-glyph skipping (depth 2) in the backtrack so the counter crosses spaces and `". "`. Per-component-length ligature tiers, **one lookup per position-restricted ligature** (so an `ignore` never shadows a neighbouring ligature), and inner-lookup positional substitution avoid the identity-substitution, equal-length-class and ignore-consumption traps. Verified in HarfBuzz: `the the the` renders three different ways; `string → s_t r i_n_g`; `office → o f f_i c e`; isolated letters never receive `.init`/`.fina`.
3. **Outlines lean on library code.** Smoothed centerline (Gaussian over arc length, σ tied to the nib) → G1 cubic path → `pathops` stroke/union/simplify → `Qu2CuPen` at 1.0 unit. No hand-written Bézier fitter in v1; an exact self-intersection test on the rounded outline, with exact-degree-elevation and simplify-fixed-point fallbacks, means the shipped outline is never self-intersecting at integer precision.
4. **One geometry per glyph.** `Outline` (closed cubic contours with explicit start points, integer font units, `draw(pen)`) is the single hand-off that compile, bounds, the TTF build and the self-check consume. The proof sheet draws from the *built binary*, so it is an honest view of what shapers see.
5. **The em-space convention is defined here (em-v1)** and becomes executable: `hwfont_schema/emspace.py` carries the text, the version constant and a validator (strokes **and** raster size) that `review` will test against.
6. **Shaping position is derived from neighbours, not from `position_in_word`**, so the store's whitespace tokenisation and the font's own word-boundary set agree by construction.
7. **Post-build self-check is part of the build:** fontTools reload, `hmtx`/charstring consistency, OS/2–hhea agreement, and a HarfBuzz expectation suite generated from the store. `uharfbuzz` is therefore a hard dependency; a failed self-check means no font on disk and exit 1.
8. **Deterministic output:** sorted classes and rules, fixed `head` timestamps via `--created-at` (default `1970-01-01T00:00:00Z`, as `ingest-segment`), byte-identical rebuilds tested.
9. **Synthetic everything.** `font_gen.synth` ships in the package (not test-only) so `font-gen demo` produces a font and a proof sheet after a plain `pip install font-gen`, before any real handwriting exists; it is also the reference implementation of em-v1.

## Precursors (early tasks in this module's plan)

**`hwfont-schema`** (backward compatible; TDD'd in that package):

- `GlyphStore.load_raster(sample) -> bytes | None` next to the existing `load_strokes`. (`all_samples(status)`, `targets()` and `load_strokes(sample)` already exist — commit `a10ba77`.)
- `hwfont_schema/emspace.py`: the §3 text as the module docstring, `EM_CONVENTION_VERSION = 1`, `RASTER_PAD = 0.05`, and
  `check_sample_em(sample: Sample, strokes: StrokeData | None, raster: bytes | tuple[int, int] | None = None) -> list[str]`
  (pure; returns violation strings, empty when conforming). `raster` is either the PNG bytes — the width/height are read from the IHDR chunk (bytes 16–24, big-endian), so `hwfont-schema` needs no Pillow — or an explicit `(width_px, height_px)`. `None` skips the raster invariants. `Metrics` and `StrokeData` docstrings reference the module.
- `StrokePoint.width: float | None = None` — the local full ink width in em at that point. Default `None` keeps every existing store, candidate set and test valid. When present it overrides the pressure→width model (§7.3 Tier A).

**Recommended upstream follow-up (not blocking):** `ingest_segment.remarkable_svg.centerline()` already holds the per-stroke mask it skeletonises; filling `StrokePoint.width` from `2 × distance_transform_edt(mask)` at skeleton pixels is the cheapest real fidelity win in the whole pipeline, because today's centerlines carry `pressure = None` and the composited page raster is a fixed-width re-render of centerlines (`compose_raster`, `_INK_WIDTH`), not real ink.

## The em-space convention (Contract Y, "em-v1")

This section is normative. `review` writes `Sample`, `Metrics` and `StrokeData` in exactly this frame; `font-gen` validates it at load. It is phrased as invariants so both sides test the same thing.

### Frame and unit

- **Cartesian, x to the right, y UP** (font convention; page pixels are y-down, so `review` flips). The schema test values `Metrics(baseline=0.0, x_height=0.5, advance=0.6, bbox=BBox(x=0, y=0, w=0.5, h=0.5))` are only consistent with y-up.
- **`1.0` = one em = the ruled row height of the capture region the sample was written in** (`Region.bbox.h` of Contract X, in page pixels; 70 px = 7.87 mm at 226 dpi on the default template, `PageConfig.line_height_px`). Scale `s = 1 / Region.bbox.h` is applied **uniformly to x and y** and is one constant for every sample of a template layout. It is a printed, physical quantity, derivable from Contract X without statistics; it keeps size relations between an `l` and an `a` as data (never per-sample "fit to box"); and it lands handwriting at the proportions in the **descriptive table** below (the one source of truth for "sane"). Row pitch (150 px) is rejected: x-height would be ≈ 0.25 em and 12 pt text would look like 6 pt.
- **Forbidden:** any per-sample scaling, rotation or normalisation. The only transforms between page px and em are the page's fiducial affine (already applied by ingest) and `s`.
- `font-gen` uses **UPM = 1000**; 1 font unit = 0.001 em.

### Origin

- `y = 0` is the row's ruled baseline (`Region.baseline_y`): `y_em = (baseline_y_px − y_px) · s`.
- `x = 0` is the sample's **glyph origin** defined by the half-gap rule: `x_em = (x_px − origin_x_px) · s` with `origin_x_px = ink.xmin_px − LSB_px` (below).

### The half-gap rule (sidebearings and advance)

Let `ink` be the sample's **centerline extent** in x (the bbox of its stroke points; for a raster-only sample, of its ink pixels; a cluster is one ink). Measured on the page in the sample's source word:

- `LSB = ½ · (ink.xmin − left_neighbour.ink.xmax)`, `RSB = ½ · (right_neighbour.ink.xmin − ink.xmax)`.
- Missing neighbour (word edge, `left_neighbor`/`right_neighbor` is `None`): the session median of the measured half-gaps (fallback `0.05` em).
- **Negative half-gaps are allowed and kept** — neighbours whose ink overlaps are nuance, not noise.
- `bbox.x = LSB`, `advance = LSB + bbox.w + RSB` (`> 0` is schema-enforced; if the arithmetic yields `≤ 0`, review clamps to `0.02` and flags the sample `marginal`).

Because advances are built from half-gaps, consecutive advances are additive: the font reproduces the page's origin-to-origin spacing exactly, independent of the nib model.

### Field meanings

| Field | Meaning in em-v1 |
|---|---|
| `Metrics.baseline` | y of this sample's baseline in its own frame: `0.0` under standard normalisation. A nonzero value is permitted (baseline bounce the reviewer chose to keep); `font-gen` translates the ink **and the stored bbox** by `−baseline` and flags `abs(baseline) > 0.1`. |
| `Metrics.x_height` | the **session** x-height in em above the baseline (`> 0`): median over the writer's x-height letters `a c e m n o r s u v w x z` of the 90th-percentile top of their centerlines. The same value is written on every sample of a session (a `T` carries it too). `font-gen` reads it only in aggregate. |
| `Metrics.advance` | distance from this origin to the next unit's origin in the source word (`> 0`), per the half-gap rule. Ink may overhang the advance (negative RSB); overhang beyond `0.15` em is flagged. |
| `Metrics.bbox` | tight box of the **centerline points** (stroke-bearing sample) or of the **ink pixels** (raster-only sample); `(x, y)` is the bottom-left corner (y-up), so `bbox.x = LSB` and `bbox.y < 0` for descenders. Visible ink exceeds it by ≈ nib/2 on every side. |
| `StrokeData.contours` | pen centerlines in writing order, one `Contour` per pen-down…pen-up, `points` in pen direction where known. Unsmoothed (`font-gen` smooths; `review` must not). `pressure ∈ [0, 1]` (1 = device maximum) or `None` for every point of the sample; mixed within a sample is a QA flag (interpolated). Optional `width` = local full ink width in em. Adjacent duplicate points allowed; a dot may be a 2-point stub. |
| `raster/<id>.png` | 8-bit grayscale (RGB tolerated), dark ink on light, covering `Metrics.bbox` **padded by `RASTER_PAD = 0.05` em on every side**: pixel `(0, 0)` (top-left) ↔ `(bbox.x − 0.05, bbox.y + bbox.h + 0.05)`; scale `S = width_px / (bbox.w + 0.10) = height_px / (bbox.h + 0.10)` px/em (recommended `S = 256`). `font-gen` infers `S` from the image size; the two scales must agree within 2 %. The pad is what keeps the nib's overhang inside the crop. |
| `Sample.kind` | must agree with the label: `single` ⇔ `len(label) == 1`, `ligature` ⇔ `len(label) ≥ 2`, and with the `Target` of the same label. `font-gen` keys everything on the label and only *checks* `kind` (`kind_mismatch`, §5). |

### Invariants (checked by `check_sample_em`; a violation is a QA flag, the sample is still used when usable)

- every stroke point: `−0.5 ≤ x ≤ 2.5`, `−0.6 ≤ y ≤ 1.4`; `0 < advance ≤ 2.5`; `0 < x_height < 1`; `−0.1 ≤ baseline ≤ 0.1`;
- stroke bbox equals `Metrics.bbox` within `0.005` em on every edge (stroke-bearing samples);
- raster, when a size is supplied: `width_px / (bbox.w + 2·RASTER_PAD)` and `height_px / (bbox.h + 2·RASTER_PAD)` agree within 2 %, and both are `≥ 32` px/em;
- pressure all-`None` or all-float within a sample; `width`, when present, in `(0, 0.2]`.

**Descriptive table** (the single source for `metrics_suspicious`, §7.5 and §8; not invariants): x-height `0.35–0.65` em, cap height `0.55–0.90`, ascender top `≤ 1.25`, descender bottom `≥ −0.45`, ink-width/advance ratio `0.3–1.2`, outline bounds within `[−600, 2500] × [−700, 1400]` units.

`EM_CONVENTION_VERSION = 1` is recorded in every QA report.

## Architecture

New package `packages/font-gen/`, depending only on `hwfont-schema` inside the repo. Layout mirrors `ingest-segment` (hatchling, `src/` layout, `__init__` re-exports, `run.py` argparse `main(argv) -> int`, plain pytest in `tests/`):

```
packages/font-gen/
  pyproject.toml
  src/font_gen/
    __init__.py             # re-exports: build_font, BuildResult, GlyphPlan, plan_glyphs, Outline, OutlineContour,
                            #             outline_from_strokes, generate_features, QAReport, Finding, synth_store,
                            #             render_proof, main, __version__ = "0.1.0"
    load.py                 # store → LoadedSample[] (em-v1 validation, strokes/raster read, kind check)
    select.py               # LoadedSample[] + Target[] → GlyphPlan (rings, positions, ligature rules, cmap)
    naming.py               # label/position/slot → glyph name; glyph order; --ring-aware grammar
    nib.py                  # width tiers, pressure→width, width smoothing, run splitting
    centerline.py           # dedupe, dot/loop detection, resample, Gaussian smoothing, Catmull-Rom → cubic path
    outline.py              # Outline value type; strokes → pathops union → Qu2CuPen → self-intersection test → Outline
    autotrace.py            # raster PNG → Outline (skimage contours), lazy import
    metrics.py              # hmtx, space, vertical metrics, ligature carets
    features.py             # GlyphPlan → .fea text (the heart of the module)
    build.py                # FontBuilder → bytes (.otf) and a second builder for .ttf
    qa.py                   # Finding/QAReport models, thresholds, severity policy, post-build self-check
    proof.py                # font bytes + rows → proof.svg via uharfbuzz + TTFont glyph set (+ .png via cairosvg)
    synth.py                # deterministic synthetic store generator (fixtures + `demo`)
    run.py                  # argparse main: build | demo | proof; output-path discipline
  tests/
    conftest.py             # synthetic store fixtures via font_gen.synth; --update-goldens
    test_emspace.py test_load.py test_select.py test_naming.py test_nib.py test_centerline.py
    test_outline.py test_autotrace.py test_metrics.py test_features.py test_shaping.py
    test_build.py test_qa.py test_proof.py test_synth.py test_run.py test_end_to_end.py test_smoke.py
```

`pyproject.toml` (the `[tool.hatch.build.targets.wheel]` block is what makes a distribution named `font-gen` ship the import package `font_gen` from `src/`; without it the wheel is empty):

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "font-gen"
version = "0.1.0"
description = "Build an OpenType font from a reviewed handwriting glyph store (Contract Y)."
requires-python = ">=3.12"
dependencies = [
    "hwfont-schema",
    "fonttools>=4.68",
    "skia-pathops>=0.9",
    "numpy>=1.26",
    "pillow>=10.0",
    "uharfbuzz>=0.40",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]
raster = ["scikit-image>=0.22"]
png-proof = ["cairosvg>=2.7"]

[project.scripts]
font-gen = "font_gen.run:main"

[tool.hatch.build.targets.wheel]
packages = ["src/font_gen"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

**Data flow:**

```
GlyphStore (store.db + strokes/*.json + raster/*.png)
  → load:      accepted samples + targets; em-v1 check (strokes + raster size); kind check   → LoadedSample[], findings
  → select:    shaping position per sample; rings per (label, position); slot order;
               ligature allowed-position sets; cmap; glyph order                          → GlyphPlan
  → outline:   per glyph: width tier → centerline prep → stroke/union → qu2cu → round →
               self-intersection test → Outline (autotrace for raster-only samples)       → {name: Outline}, findings
  → metrics:   hmtx (advance from store, lsb from outline), space, vertical metrics, carets
  → features:  GlyphPlan → .fea text + shaping expectations
  → build:     FontBuilder (CFF) + feaLib → bytes  [+ second FontBuilder (TTF) → bytes]
  → qa:        self-check on the bytes (fontTools reload + HarfBuzz suite); report; severity policy
  → proof:     render_proof(font_bytes, rows) → SVG (+ PNG)
  → write:     Family.otf (+ .ttf), Family.fea, Family.qa.json, proof.svg (+ .png) — only after every check passed
```

`select`, `naming`, `nib`, `centerline`, `outline`, `metrics`, `features`, `build`, `proof.render_proof` are pure functions of plain data; I/O is confined to `load`, `run`, and the writers in `qa`/`proof`.

**Core intermediate types** (frozen dataclasses):

```python
LoadedSample(sample: Sample, strokes: StrokeData | None, raster: bytes | None, position: Pos, flags: tuple[str, ...])
GlyphSpec(name: str, sample_id: str | None, label: str, kind: Kind, ring_key: tuple[str, Pos | None], slot: int, source: str)
GlyphPlan(glyphs: tuple[GlyphSpec, ...],                 # final glyph order, .notdef first
          rings: dict[tuple[str, Pos | None], tuple[str, ...]],   # ring key → glyph names in slot order (n ≥ 1)
          ring_n: int,                                   # RING_N in force (--ring)
          transparent: frozenset[str],                   # glyphs that never cycle (n == 1 rings, space)
          letters: frozenset[str],                       # every glyph form of alphabetic labels + quotesingle forms
          ligatures: dict[str, tuple[tuple[str, ...], frozenset[Pos]]],   # lig base name → (component base names, allowed positions)
          positional: dict[str, dict[Pos, str]],         # base name → {Pos: positional base name}
          cmap: dict[int, str])
OutlineContour(start: tuple[int, int], segments: tuple[Segment, ...])   # Segment = ("line", p) | ("curve", c1, c2, p)
Outline(contours: tuple[OutlineContour, ...],
        bounds: tuple[int, int, int, int] | None)        # xMin, yMin, xMax, yMax; None for empty
Finding(severity: Literal["error", "warn", "info"], code: str, message: str,
        label: str | None, sample_id: str | None, glyph: str | None, data: dict)
```

`Pos` is `Literal["init", "medi", "fina", "isol"]`. **Every `OutlineContour` is closed:** `Outline.draw(pen)` emits `moveTo(start)`, then each segment (`lineTo(p)` / `curveTo(c1, c2, p)`), then `closePath()`; the last segment's endpoint may or may not equal `start` (if it does not, `closePath` closes with a straight line, which the simplify step never produces for stroked ink but which the type permits). `draw` replays onto any fontTools pen (`T2CharStringPen`, `BoundsPen`, `AreaPen`, `SVGPathPen`, `Cu2QuPen`, `RecordingPen`, a `pathops.Path` pen).

## Loading (`load.py`)

1. `GlyphStore.open(root)`; `store.all_samples(ReviewStatus.accepted)`; `store.targets()`; `store.coverage()` (reported verbatim under `coverage`, see §5 for how `met` is recomputed).
2. Per sample: `store.load_strokes(sample)` / `store.load_raster(sample)`. A non-`None` path whose file is missing or unparseable → `sidecar_missing` (warn; the sample is excluded from rings and counted). Neither strokes nor raster → `sample_no_ink` (warn; excluded).
3. `hwfont_schema.emspace.check_sample_em(sample, strokes, raster)` → each violation is a `convention_violation` (warn) carrying the string; the sample is still used. A raster whose scales disagree by more than 2 % is additionally excluded from the raster fallback (`raster_scale_mismatch`, warn) because it cannot be georeferenced.
4. **Kind check:** `sample.kind` must match the label length rule and the `Target` of the same label (when one exists); otherwise `kind_mismatch` (warn). Naming, eligibility and ligature detection key purely on `label` (`isalpha`, length), so a mismatched sample is still used — the flag tells the owner the store disagrees with itself.
5. **Shaping position** `position = shaping_position(sample.context)`: `before = is_letterish(left_neighbor)`, `after = is_letterish(right_neighbor)` where `is_letterish(ch) = ch is not None and (ch.isalpha() or ch == "'")`; `(1,1) → medi`, `(1,0) → fina`, `(0,1) → init`, `(0,0) → isol`. This is the same boundary set the font's `@LETTER` class uses (§9.1), so `fox.` yields a final `x` in both places even though `ingest_segment.derive_context` tokenises on whitespace and labels that `x` medial. Disagreement with `context.position_in_word` → `position_rederived` (info).
6. Store digest: SHA-256 over the sorted `(id, data)` rows of `sample` plus the `target` rows → `name` table version string and the report, so a font is traceable to the exact store state.

## Selection and ring assembly (`select.py`)

**Eligibility.** `review_status == accepted`, `quality ∈ {good, marginal}`, and ink present. `bad` → `bad_sample_excluded` (info; the reviewer's explicit verdict, listed not hidden). Other statuses are never read (counted in the report from `coverage()`).

**Rings.** A ring is the ordered list of variants that cycling rotates through; ring key = `(label, position)` with `position ∈ {None, init, medi, fina, isol}`. Per label:

1. Group eligible samples by shaping position.
2. **Positional rings exist only for alphabetic labels** (`label.isalpha()`; true for every ligature target). Digits and punctuation put all samples in the default ring.
3. **Default position** = `medi` if `count(medi) ≥ POS_MIN`, else the position with the most samples (ties broken `medi, init, fina, isol`). The **default (unsuffixed) ring** = samples of the default position plus samples of every position with `count < POS_MIN`.
4. Every other position with `count ≥ POS_MIN = 3` becomes a positional ring `label.<pos>`. Consequence: every eligible sample maps to exactly one glyph (no duplicated outlines), and the cmap glyph renders the writer's medial form whenever three or more medial samples exist — the right fallback for apps that run no GSUB.
5. Slot order inside a ring: `good` before `marginal`; **slot 0 is the medoid** of the good samples (L1 distance on `(advance, bbox.w, bbox.h)` each divided by the ring's median; ties by `id`); then remaining goods by `id`, then marginals by `id`. Truncate to `RING_N` (default 7); surplus → `ring_surplus` (info, lists ids). A ring reaching `≥ 2` only via marginals → `marginal_used` (info).
6. `n_ring ≥ 2` → ring member; `n_ring == 1` → **transparent** (never cycled, skipped by the cycling context) and `ring_singleton` (warn: this glyph will look stamped); `n_ring < RING_N` → `ring_short` (info with `n`).

**Numbers.** `RING_N = 7`: prime, so word lengths 2–6 (plus a space) never resonate with the counter (with 4, `the ` has period 4 and every `the` is identical; with 6, a repeated 3-letter word alternates ABAB); 7 is what the capture target (12 samples per letter, 8 per cluster) realistically delivers after review and position splitting; GSUB cost is linear in it. `POS_MIN = 3`: a positional ring of 1–2 would stamp every word-final `e`. Both are overridable: `--ring N` with `2 ≤ N ≤ 13` (a composite `N` → `ring_not_prime`, info); `--pos-min M ≥ 2`.

**Ligatures** are rings like any other (label `fi` → base `f_i`), subject to the same positional-ring rule, plus an **allowed-position set** = the shaping positions in which the label has `≥ 1` eligible sample (§9.3). A cluster captured in all four positions gets an unconditional rule; a final-only `ing` never fires mid-word. Each restricted ligature → `ligature_restricted` (info listing positions). Ligature components are the label's characters mapped to base names; a component with no glyph in the font → `ligature_component_missing` (warn; the ligature glyph is still emitted, no rule).

**Targets.** A `Target` with zero eligible samples → `target_no_samples` (warn; no glyph, no cmap entry — the OS falls back to another font rather than showing a blank; the proof sheet draws a red box). **Eligible** count below `required_count` → `target_below_required` (info; `data = {required, accepted, eligible}` where `accepted` is `coverage()`'s count of every accepted sample regardless of quality or ink and `eligible` is this module's count; the report's `coverage` rows carry both numbers and `met_eligible = eligible ≥ required` next to the store's `met`, so a target cannot read "met" while contributing fewer usable samples than required). If no ring at all can be built → `empty_font` (error).

**Synthetic glyphs.** `.notdef` (glyph 0, 500-unit hollow box), `space` (empty; §8).

## Glyph naming (`naming.py`)

Grammar: `<base>[.<pos>][.alt<k>]`, `pos ∈ {init, medi, fina, isol}`, `k ∈ 1..RING_N−1` (so `1..6` by default; `--ring N` extends it to `1..N−1`, and the same `N` sizes `@ST0..@ST{N−1}` and `STEP1..STEP{N−1}` in §9).

- Singles: AGL name via `fontTools.agl.UV2AGL[ord(ch)]` (`a`, `A`, `zero`, `period`, `comma`, `exclam`, `question`, `quotesingle`, `quotedbl`, `hyphen`, `semicolon`, `colon`, `parenleft`, `parenright`); fallback `uniXXXX`. Digits therefore never start a name with a digit.
- Ligatures: component names joined by `_` — AGL ligature convention — `f_i`, `f_f_i`, `e_f_t`, `f_o_r_e`, `o_u_g_h`, `t_i_o_n`, `T_h`, `S_t_r`, `S_c_h`. PDF text extraction and copy/paste from apps that use glyph names decompose `t_i_o_n` back to `tion` and strip the `.pos`/`.alt` suffixes by the same rule.
- Examples: `a`, `a.alt3`, `a.init`, `a.init.alt2`, `t_i_o_n.fina`, `S_c_h.alt1`, `quotesingle.alt6`. Longest in the default target set at `--ring 7`: `parenleft.alt6` / `S_c_h.fina.alt6` (15 chars); at the cap `--ring 13`: `S_c_h.fina.alt12` (16). All `≤ 31` chars, `[A-Za-z0-9._]` only, unique — asserted by `naming.validate(plan)` (`glyph_name_invalid`, error).
- Reserved: `.notdef`, `space`.
- **cmap:** only base glyphs (singles' default-ring slot 0, `space`); alternates, positional forms and ligatures are reachable only through GSUB, so extracted text is the typed text. Aliases onto existing glyphs: U+00A0 → `space`, U+2018/U+2019 → `quotesingle`, U+201C/U+201D → `quotedbl`, U+2010/U+2013 → `hyphen`.
- **Glyph order** (deterministic): `.notdef`, `space`, singles by codepoint — each followed by its alternates, then its positional bases and their alternates — then ligatures by component-codepoint tuple, same expansion.
- Worst case ≈ 94 singles × 5 rings × 7 + 50 clusters × 5 × 7 ≈ 5 000 glyphs; realistic ≈ 1 000–1 500.

## Outline generation (`nib.py`, `centerline.py`, `outline.py`, `autotrace.py`)

All geometry below is in float font units (em × 1000) until the final rounding.

### 7.1 Frame

Points → `X = x · 1000`, `Y = (y − metrics.baseline) · 1000`. The stored `Metrics.bbox` is translated by the same `−baseline` before any comparison with the outline (§7.5), so a `baseline_nonzero` sample does not also trip `metrics_inconsistent`.

### 7.2 Centerline preparation (`centerline.py`)

The input centerlines are what the ingest adapter produces today: skeletonised, Douglas–Peucker-simplified at ε = 1 px (= **14 font units** on the 70 px row), vertices quantised to the pixel grid. They are noisy samples of a smooth pen path and are treated as such:

1. **Dedupe:** drop consecutive points closer than `0.5` units.
2. **Dots:** a contour whose total length is `< 0.5 · NIB` (stubs, `i`-dots, full stops) becomes a **stroked stub**: a 2-point segment of length `max(length, 1)` unit along the contour's principal direction (`+x` when undefined), centred on the contour's centroid, passed through the same `stroke(w, ROUND_CAP, ROUND_JOIN, 4.0)` as every other stroke with `w` = the glyph's nib width (Tier A/B: the mean of the contour's widths). The stroker's round caps *are* the disc (verified: one contour, area 1590.75 vs πr² 1590.43 for the 45-unit nib). `font-gen` never calls `Path.conicTo` — skia-pathops 0.9.2 stores the wrong control point for axis-aligned quarter arcs (verified: an 18 % area loss). The contour skips the rest of this section (`dot_rendered`, info).
3. **Closed loops:** endpoint distance `< 1.5 · NIB` and length `> 6 · NIB` → the contour is closed (periodic smoothing, no caps, no seam notch). This rejoins loops the skeleton tracer opened.
4. **Resample** at uniform arc length `RESAMPLE = 8` units by linear interpolation (≈ 0.06 mm on the 7.87 mm em; below any visible feature).
5. **Smooth** `x` and `y` with a Gaussian over arc-length index, `σ = SMOOTH_SIGMA · NIB` with `SMOOTH_SIGMA = 0.25` (≈ 11 units ≈ 0.8 px at the default nib). Open contours use reflect padding so endpoints are pinned; closed contours wrap. The smoothing scale is tied to the pen, not to pixels: wiggles finer than a quarter of the stroke's own width are invisible in real ink, while tremor with wavelength `> ~0.5 mm` survives. `--smooth-sigma 0` disables it for a future adapter that delivers true device samples. (Verified consequence of *not* smoothing: stroking the raw polyline leaves 163 straight facets on a test "e + stem" glyph that `Qu2CuPen` cannot merge; stroking a smoothed cubic centerline leaves 5 lines and 98 cubics.)
6. **Cubic centerline:** Catmull-Rom through the smoothed knots (tension 1/6) → a G1 cubic `pathops.Path`; closed contours close with wrap-around tangents. Knot indices are kept so width runs (§7.4) can split the path exactly at knots.

### 7.3 Width tiers (`nib.py`)

`NIB = 0.045` em (45 units, `--nib-em`): a 0.4 mm fineliner on the 7.87 mm row is 0.051 em, and the reMarkable's rendered fineliner measures 2–3.5 px = 0.03–0.05 em; 0.045 sits between. Per point, in priority order:

- **Tier A — `StrokePoint.width` present:** `w_i = width_i · 1000`, clamped to `[0.4, 2.0] · NIB`. The nib model is bypassed; this is exact device width.
- **Tier B — pressure present:** `w(p) = NIB · (0.7 + 0.6 · p)`, so `[0.7, 1.3] · NIB` — a visible pressure cue without turning into a brush. Mixed `None`/float within a sample → interpolate the gaps (`pressure_partial`, warn).
- **Tier C — neither (today's reality):** constant `w = NIB`; `nib_width_assumed` (info, once per build).
- **Tier D — raster only:** §7.6.

Tier A/B width profiles are interpolated along the resampled arc length, smoothed with the same Gaussian as the centerline, and slope-clamped to `|dw/ds| ≤ 0.7` so run boundaries never degenerate.

### 7.4 Stroking and union (`outline.py`)

- **Tier C:** one call: `path.stroke(NIB, LineCap.ROUND_CAP, LineJoin.ROUND_JOIN, 4.0)` on the whole cubic centerline. Exact, no runs, no ledges.
- **Tier A/B (variable width):** split the cubic centerline at knots into **runs** whose width stays within `±RUN_TOL = 3 %` of the run's first knot width; stroke each run at its mean width with round caps/joins; adjacent runs share the boundary knot so round caps cover the joint. The ledge between adjacent runs is `≤ 0.03 · w / 2 ≈ 0.7` units at `NIB` — below the 1-unit integer rounding of the charstring and invisible at any size. A full pressure sweep yields ≈ 40 runs; measured union cost ≈ 2 ms per glyph at 6 % (≈ 4 ms at 3 %).
- Every piece: `convertConicsToQuads()` (stroke emits conics). Then one union for the whole glyph: `out = pathops.Path(); pathops.union(pieces, out.getPen()); out.simplify(fix_winding=True, keep_starting_points=False, clockwise=False)` — counter-clockwise outer contours for CFF (verified: `AreaPen` positive). Crossing strokes merge into one contour; counters (the hole in `o`) come out as correctly wound inner contours. A font cannot express ink darkening at overlaps — the one inherent loss, recorded here, not in QA.

### 7.5 Quad → cubic, rounding, and the self-intersection test

1. `out.draw(Qu2CuPen(RecordingPen(), max_err=QU2CU_ERR, all_cubic=True))` with `QU2CU_ERR = 1.0` unit. 1.0 is invisible at any size; measured cost 98 cubics / 616 charstring bytes on the test glyph versus 63 / 411 at 2.0, and 2.0 starts to flatten the eye of an `e` (≈ 100 units across). Why `qu2cu` and not a hand-written Schneider fitter: it ships in fontTools, is exact at `max_err`, merges consecutive quads into single cubics, and is tested upstream.
2. Round every coordinate to integers → candidate `Outline`.
3. **Self-intersection test (exact, on the rounded path).** Replay the candidate into a fresh `pathops.Path` `P`; `Q = copy(P); Q.simplify(fix_winding=True, keep_starting_points=True, clockwise=False)`. `simplify` inserts a vertex at every crossing and removes every degenerate loop, so the candidate is free of self-intersections **iff** `Q` has the same contour count as `P`, the same segment count per contour, and every on-curve and control point within `0.5` unit of `P`'s. This is the test that catches the sub-unit loops that integer rounding or the `max_err = 1.0` approximation can create; it is exact at the precision the font is shipped at.
4. **Drift check (heuristic):** `|area(Q) − area(out)| ≤ 1 %` of `area(out)` and equal contour counts against the quad union — a gross-error detector for the approximation, not a correctness proof.
5. **Fallbacks, in order,** each rounded and re-tested by steps 3–4: (a) the **exact degree elevation** of the quads (`c1 = p0 + ⅔(q − p0)`, `c2 = p2 + ⅔(q − p2)`; the same curve before rounding, so it introduces nothing — rounding still can, hence the re-test); (b) the **simplify fixed point**: take `Q` itself as the next candidate (its coordinates rounded), re-test, repeat up to 3 times — `simplify` output is intersection-free at float precision, so this converges whenever rounding is the only culprit. The first candidate failing → `outline_fit_fallback` (warn, says which fallback shipped). If (b) does not converge → `outline_unstable` (warn) and the last `Q` ships (any residual artefact is sub-unit). If any step reports zero area → `outline_empty` (warn; an empty glyph is emitted under the name so GSUB stays valid).
6. **Degeneracy checks** on the final `Outline`: empty or `|area| < (0.3 · NIB)²` → `outline_degenerate` (warn, empty glyph emitted); a contour with `|area| < (0.3 · NIB)²` → `outline_tiny_contour` (info, kept — a real pen leaves ticks); `> 12` contours → `outline_fragmented` (warn); a stroke with `< 2` distinct points → `stroke_degenerate` (info, skipped within the glyph); bounds outside the descriptive table's `[−600, 2500] × [−700, 1400]` → `metrics_suspicious`; outline bbox versus the **baseline-translated** `Metrics.bbox` expanded by `NIB/2` differing by `> 0.05` em on any edge → `metrics_inconsistent` (warn; the outline is trusted, the store's bbox is not).

### 7.6 Raster fallback (`autotrace.py`, lower priority)

Only when `strokes is None` and `raster` is present and georeferenceable (§5.3): Pillow → grayscale → Otsu threshold (`skimage.filters.threshold_otsu`; fixed 128 when scikit-image is absent) → `skimage.measure.find_contours(level=0.5)` → polygons in pixel space → affine onto `Metrics.bbox` padded by `RASTER_PAD` (§3) → the same Gaussian smoothing (pixel contours are stair-stepped) → drawn into `pathops.Path`, `simplify(fix_winding=True, clockwise=False)` → `Qu2CuPen(max_err=1.5)` → §7.5 steps 2–6. Every such glyph → `raster_fallback` (warn); it is never slot 0 of a ring when a stroke-sourced sample exists. scikit-image missing → `raster_unavailable` (warn; empty glyph).

### 7.7 Why not the exact tapered-capsule envelope now

The exact envelope of a round pen with linearly varying radius is the union of `ConvexHull(D(p_i, r_i) ∪ D(p_{i+1}, r_{i+1}))` over resampled segments — scallop-free and provably correct. But its boundary is lines and arcs at the resample pitch, which `qu2cu` cannot merge, so it needs a hand-written corner-aware least-squares fitter to avoid hundreds of segments per glyph. That is the v2 upgrade for Tiers A/B once real width data exists (§Out of scope); v1 ships the library path with the sub-unit ledge documented above.

## Metrics (`metrics.py`)

- `hmtx`: advance = `round(Metrics.advance × 1000)`; LSB = `Outline.bounds.xMin` (CFF requires `hmtx` and charstring to agree; negative values are legal and expected for overlapping letters); empty outlines `lsb = 0`.
- Flags: advance outside `[0.3, 3.0] ×` median advance of the same kind → `advance_outlier` (warn); ink overhang beyond `advance + 0.15` em → `metrics_suspicious`; `Metrics.x_height` deviating `> 25 %` from the median → `x_height_outlier` (warn); per-sample values outside the §3 descriptive table → `metrics_suspicious` (warn).
- **`space`** = `round(0.5 × median advance of lowercase singles)` clamped to `[200, 400]` units (`--space-em` overrides); `space_advance_assumed` (info with the value) — inter-word gaps are not captured in Contract Y.
- **Vertical metrics:** `ascender = max(800, ceil10(max yMax over all glyphs))`, `descender = min(−250, floor10(min yMin))`, `lineGap = 0`; identical `hhea`, `typo` and `win` values; `fsSelection` `USE_TYPO_METRICS` (bit 7) and `REGULAR` (bit 6). Flourishes never clip; the floors keep sane line height on sparse stores. `sxHeight` = median `Metrics.x_height × 1000` of lowercase singles (fallback: median `yMax` of `a c e m n o r s u v w x z`); `sCapHeight` = median `yMax` of `H I T E F L` slot-0 outlines (fallback all capitals). `xAvgCharWidth` via `recalcAvgCharWidth`; Unicode/codepage ranges via `recalcUnicodeRanges`/`recalcCodePageRanges`. OS/2 version 4, `usWeightClass 400`, `fsType 0`, `achVendID "HWFT"`.
- **Ligature carets** (`GDEF LigatureCaretByPos`): at component boundaries placed proportionally to the components' median advances (fallback: even spacing).

## OpenType feature design (`features.py`) — the core

### 9.1 Principles that make things fire by default

1. **Features:** ligatures under `liga` **and** `clig`; positional forms and cycling under `calt`; manual access under `aalt`. `liga`/`clig`/`calt` are on by default in the HarfBuzz hosts (Blink: Chrome/Edge/Electron; Gecko: Firefox; LibreOffice; Android/Linux), CoreText (TextEdit, Pages, Safari, Mail) and DirectWrite (Word, WPF, UWP/WinUI). **Word is the exception:** ligatures and contextual alternates depend on the document's compatibility mode and the Font → Advanced dialog, and "Use Contextual Alternates" should be assumed off; the base-glyph fallback (§9.10) is therefore a first-class rendering, and `docs/` carries a one-line setup note. Whether a host shapes `calt` *across spaces* is a separate question (§Known risks 4).
2. **Script registration:** `languagesystem DFLT dflt; languagesystem latn dflt;` before any lookup. feaLib registers every feature under both; a missing `latn` is the classic reason "ligatures work in FontGoggles but not in Word/macOS".
3. **Lookup order is LookupList order**, not feature order: shapers collect the lookups of all enabled features and apply them by index; feaLib assigns indices in definition order. The file therefore defines lookups in the order they must run: `LIGA_4, LIGA_4_* → LIGA_3, LIGA_3_* → LIGA_2, LIGA_2_* → TO_* → POS → STEP_* → CYCLE`. (feaLib moves `aalt`'s lookups to the front of the list; order among the rest is preserved — harmless.)
4. **Inner lookups instead of class-to-class substitution** for `POS` and `CYCLE` (`sub … @BASE' lookup TO_MEDI …`). A chain rule that matches *consumes the position* even when the inner lookup has no entry for that glyph — exactly the "keep the base form" behaviour wanted for labels lacking a positional ring — with no identity substitutions, no equal-length-class bookkeeping and no duplicated outlines. (feaLib syntax, verified: the `lookup X` reference goes immediately after the marked glyph, before the lookahead.)
5. **`@LETTER` contains every glyph of every alphabetic label in every form** (base, `.pos`, `.alt`, ligature glyphs and their forms) plus every form of `quotesingle`, so `don't` stays one word. **Trade-off, stated:** straight-quote *quotation marks* are then word-internal — in `'the'` the `t` and `e` take medial forms and the quotes step the counter. Curly quotes are aliased onto the same glyph (§6), so `‘the’` behaves the same. The apostrophe case is far more frequent in running text than straight-quoted words, so `quotesingle` stays in `@LETTER`; the proof sheet carries a quoted-word row so the owner sees the consequence. Hyphen, digits, space and all other punctuation are word boundaries. If a form is missing from `@LETTER`, the glyph after it is mis-positioned (verified: a stub glyph left out of `@LETTER` made the following `a` lose its final form), so the class is generated from the plan and tested for completeness.
6. **No `lookupflag`s, no marks, no GPOS**; an explicit `GDEF` with glyph classes (base/ligature) and ligature carets so the caret can be placed inside `t_i_o_n`.
7. **Deterministic output:** classes and rules in sorted order; the same store produces a byte-identical `.fea` (golden-file tested). **Lookups that would be empty are omitted together with every rule that references them — this is mandatory, not cosmetic:** an unreferenced empty `lookup X { } X;` compiles (feaLib drops it), but a chain rule referencing an empty named lookup (`sub @BASE' lookup EMPTY;`) aborts compilation with `FeatureLibError: 'NoneType' object has no attribute 'lookup_index'` (verified, fontTools 4.68.0). `feature_compile_failed` (error) is the symptom of getting this wrong; `test_features` covers every `TO_*`/`STEP*` being empty.

### 9.2 Classes

```
@LETTER       every form of every alphabetic label (+ quotesingle forms)     — context only
@BASE         slot-0 glyph of every default ring (singles and ligatures)     — POS input
@ST0 … @ST{N−1}  cycling state classes: @STk = { ring[k mod n_ring] for every ring with n ≥ 2 }
@TRANSPARENT  every glyph of an n == 1 ring, plus space                      — skipped by CYCLE's backtrack
```

### 9.3 Ligatures — tiers `LIGA_4`, `LIGA_3`, `LIGA_2` plus one lookup per restricted ligature

- Rules are chain rules with inline `by`; feaLib builds anonymous `LigatureSubst` lookups behind each chain lookup (verified).
- **Tiers by component length, longest first,** so `f_f_i` is tried before `f_i` and `s_t_r` before `s_t` (lookup order, §9.1.3).
- **One shared lookup per tier (`LIGA_n`) holds the unconditional ligatures** of that length — plain `sub C' by L;` rules, no `ignore`. Within one lookup HarfBuzz matches leftmost-first, so two unconditional ligatures competing for a glyph (`ng`/`gh` in `angha`) resolve to the leftmost (`n_g`).
- **Every position-restricted ligature gets its own lookup `LIGA_n_<L>`** (e.g. `LIGA_2_f_f`, `LIGA_4_t_i_o_n`), emitted after the tier's shared lookup, sorted by component tuple. Rationale (verified): a matched `ignore sub` consumes its whole input *within the lookup it lives in*. In one shared lookup, `ignore sub @LETTER f' f' @LETTER` (an `ff` captured only word-finally) consumes both `f`s in `office` and `f_i` is never tried (`o f f i c e`); likewise a restricted `st` blocks `t_h` in `asthma` and a restricted `ng` blocks `g_h` in `Bingham`. With the restricted ligature isolated in its own lookup, its `ignore` affects nothing else: `office → o f f_i c e`, `affix → a f f_i x`, `asthma → a s t_h m a`, `Bingham → B i n g_h a m`, `angha → a n g_h a` (with `ng` restricted, `gh` unconditional), while `agha → a g_h a`, `sing → s i n_g`, `staff → s t a f_f`, `best → b e s_t`, `stiff → s t i f_f` are unchanged. The same isolation across tiers is what lets `string → s_t r i_n_g` when `s_t_r` is final-only.
- **Precedence consequence, documented:** within a tier, unconditional ligatures (shared lookup, applied first) win over restricted ones competing for the same glyph; across tiers, longer wins. Every pair of ligatures that can compete for a glyph (one label's suffix equals another's prefix, or one label contains another) → `ligature_overlap` (info, stating the resolution).
- **Positional restriction** uses the four mutually exclusive contexts `(letter before?, letter after?)`: medial `(1,1)`, final `(1,0)`, initial `(0,1)`, isolated `(0,0)`. "No letter after" cannot be matched positively, so the rules for one ligature are emitted in this fixed order inside its own lookup and rely on first-match-wins (`C'` = marked components):

```
medial  → sub @LETTER C' @LETTER by L;   or   ignore sub @LETTER C' @LETTER;
final   → sub @LETTER C' by L;           or   ignore sub @LETTER C';
initial → sub C' @LETTER by L;           or   ignore sub C' @LETTER;
isolated→ sub C' by L;                   or   (no rule)
```
  All four allowed → the ligature is unconditional and lives in the shared tier lookup instead. At most 4 rules per restricted ligature; ≤ 3 shared + ≤ 50 restricted chain lookups (+ their anonymous ligature lookups) for the default 50-cluster target set — ≈ 110 GSUB lookups, ordinary for a text font.
- Ligatures never cross word boundaries (space/punctuation are never components). After the `LIGA` lookups the ligature glyph is an ordinary ring base: it takes a positional form in `POS` and cycles in `CYCLE` (verified: `sing → s i_n_g.alt1`).

### 9.4 Positional forms — `POS` with inner lookups `TO_MEDI`, `TO_FINA`, `TO_INIT`, `TO_ISOL`

```
lookup POS {
  sub @LETTER @BASE' lookup TO_MEDI @LETTER;
  sub @LETTER @BASE' lookup TO_FINA;
  sub @BASE' lookup TO_INIT @LETTER;
  sub @BASE' lookup TO_ISOL;
} POS;
```

Each `TO_*` lists only the bases that have that positional ring (`sub e by e.fina;`). Processing is left to right: the backtrack sees already-substituted forms (hence 9.1.5), the lookahead sees unprocessed base glyphs (all in `@LETTER`). The four contexts are disjoint, so an isolated letter whose label has only an `.init` ring is consumed unchanged by the `TO_ISOL` rule and never becomes `.init` (the defect the class-based `ignore` formulations carry). A `TO_*` with no entries is omitted with its rule (§9.1.7).

### 9.5 Ligatures ↔ positions

`LIGA` runs before `POS` so a ligature glyph is positioned as one unit (`nation → n a.alt1 t_i_o_n` with `t_i_o_n` final-only; `nations → … t.alt2 i.alt3 o.alt4 n.alt5 s.alt6`, no ligature). If `POS` ran first, the ligature rules would have to enumerate positional component combinations.

### 9.6 Variant cycling — `STEP1..STEP{N−1}` and `CYCLE`

GSUB has no memory other than glyph identity, so the previous glyph's ring slot *is* the counter:

```
lookup STEPk { sub <base> by <ring[k mod n_ring]>; … }   # one entry per ring whose slot k ≠ base
lookup CYCLE {
  sub @ST0 @ST0' lookup STEP1;
  sub @ST0 @TRANSPARENT @ST0' lookup STEP1;
  sub @ST0 @TRANSPARENT @TRANSPARENT @ST0' lookup STEP1;
  … the same three shapes for @ST1→STEP2 … @ST{N−2}→STEP{N−1} …
  # state N−1 → next glyph stays slot 0: no rule
} CYCLE;
```

- **Transparent skipping** (`@TRANSPARENT` in the backtrack at depth 1 and 2) carries the counter across a space or `". "` — this is what defeats word-level stamping: verified `the the the → t h.alt1 e.fina.alt2 ␣ t.alt3 h.alt4 e.fina.alt1 ␣ t.alt2 h.alt3 e.fina`. Three or more transparent glyphs in a row reset the counter (harmless). Depth 2 costs 3 rules per state; depth 3 would be 4 and is not worth it. Punctuation with its own ring (12 samples per mark in the default target set) is a ring member, so it both cycles and steps the counter.
- **Short rings pad by modulus:** a 2-variant `z` has slots `[z, z.alt1, z, z.alt1, …]`, so `z` and `z.alt1` appear in several `@STk`; the first matching rule (lowest state) wins, which re-synchronises the counter modulo `n_ring` (verified: `zzzz → z z.alt1 z z.alt1`). A `STEPk` with no entries (every ring's slot k is its base) is omitted with its three rules.
- Ligature and positional glyphs are ring bases in `@ST0`, so cycling composes with both (verified: `aaaaaaaa → a.init a.alt1 … a.alt6 a.fina`).
- `--cycle-mode word` drops the `@TRANSPARENT` rules (counter restarts at every word) — the fallback if a host is found not to shape `calt` across spaces (§Known risks 4).
- Why not `rand`: HarfBuzz ≥ 2.0 supports it, CoreText and DirectWrite do not. Why not the cookbook equal-length class rotation (`sub @alt1 @default' by @alt2`): it needs every glyph to have N names (duplicate outlines or identity substitutions) and resets at every single-variant glyph. Why not a previous-glyph hash walk: the variant becomes a pure function of the preceding glyph, so every word after a space unfolds identically — repeated words are stamped by construction.

### 9.7 `aalt`, `GDEF`, carets

`aalt` enumerates each base's alternates (`sub a from [a.alt1 … a.init a.init.alt1 … a.fina];`) so glyph palettes in InDesign/Illustrator/macOS expose them. `GDEF` declares base vs ligature glyph classes and `LigatureCaretByPos` for every ligature base (§8).

### 9.8 The `.fea` skeleton (as generated; names from the default target set, `--ring 7`)

```fea
# Generated by font-gen 0.1.0 from store <digest12> — do not edit; regenerate.
languagesystem DFLT dflt;
languagesystem latn dflt;

# ---------- classes ----------
@LETTER = [a a.alt1 a.alt2 a.alt3 a.alt4 a.alt5 a.alt6 a.init a.init.alt1 a.init.alt2 a.fina
           b b.alt1 ... z z.alt1  A A.alt1 ...  f_i f_i.alt1 f_i.alt2 f_i.alt3  t_i_o_n t_i_o_n.alt1
           i_n_g i_n_g.alt1 i_n_g.alt2  T_h T_h.alt1 ... S_c_h  quotesingle quotesingle.alt1 ...];
@BASE   = [a b ... z A ... f_i t_i_o_n i_n_g T_h ... S_c_h];
@ST0 = [a a.init b ... z f_i t_i_o_n i_n_g period ...];           # slot 0 of every ring (n >= 2)
@ST1 = [a.alt1 a.init.alt1 b.alt1 ... z.alt1 f_i.alt1 t_i_o_n.alt1 i_n_g.alt1 period.alt1 ...];
@ST2 = [a.alt2 a.init.alt2 b.alt2 ... z f_i.alt2 t_i_o_n i_n_g.alt2 ...];   # z: 2 mod 2 = 0
@ST3 = [a.alt3 a.init b.alt3 ... z.alt1 f_i.alt3 t_i_o_n.alt1 i_n_g ...];
@ST4 = [...]; @ST5 = [...]; @ST6 = [...];
@TRANSPARENT = [space a.fina Q X ...];                             # every glyph with exactly one variant, plus space

# ---------- ligatures: tiers longest first; shared lookup = unconditional; one lookup per restricted ligature ----------
lookup LIGA_4 {
  sub f' o' r' e' by f_o_r_e;                       # captured in all four positions → unconditional
} LIGA_4;
lookup LIGA_4_o_u_g_h {                             # "ough": captured only word-finally
  ignore sub @LETTER o' u' g' h' @LETTER;
  sub    @LETTER o' u' g' h' by o_u_g_h;
  ignore sub o' u' g' h' @LETTER;
} LIGA_4_o_u_g_h;
lookup LIGA_4_t_i_o_n {
  ignore sub @LETTER t' i' o' n' @LETTER;
  sub    @LETTER t' i' o' n' by t_i_o_n;
  ignore sub t' i' o' n' @LETTER;
} LIGA_4_t_i_o_n;

lookup LIGA_3 {
  sub f' f' i' by f_f_i;
  sub f' f' l' by f_f_l;
  sub s' t' r' by s_t_r;  sub t' c' h' by t_c_h;  sub d' g' e' by d_g_e;  ...
} LIGA_3;
lookup LIGA_3_S_c_h { sub S' c' h' @LETTER by S_c_h; } LIGA_3_S_c_h;          # initial only
lookup LIGA_3_S_t_r { sub S' t' r' @LETTER by S_t_r; } LIGA_3_S_t_r;
lookup LIGA_3_e_f_t {                                                        # "eft": medial + final captured
  sub @LETTER e' f' t' @LETTER by e_f_t;
  sub @LETTER e' f' t' by e_f_t;
  ignore sub e' f' t' @LETTER;
} LIGA_3_e_f_t;
lookup LIGA_3_i_n_g {                                                        # "ing": final only
  ignore sub @LETTER i' n' g' @LETTER;
  sub    @LETTER i' n' g' by i_n_g;
  ignore sub i' n' g' @LETTER;
} LIGA_3_i_n_g;

lookup LIGA_2 {
  sub f' i' by f_i;  sub f' l' by f_l;  sub s' t' by s_t;  sub t' h' by t_h;  sub g' h' by g_h;  ...
} LIGA_2;
lookup LIGA_2_C_h { sub C' h' @LETTER by C_h; } LIGA_2_C_h;
lookup LIGA_2_S_t { sub S' t' @LETTER by S_t; } LIGA_2_S_t;
lookup LIGA_2_T_h { sub T' h' @LETTER by T_h; } LIGA_2_T_h;
lookup LIGA_2_f_f {                                                          # "ff": final only
  ignore sub @LETTER f' f' @LETTER;
  sub    @LETTER f' f' by f_f;
  ignore sub f' f' @LETTER;
} LIGA_2_f_f;
lookup LIGA_2_l_l { sub @LETTER l' l' by l_l; ... } LIGA_2_l_l;              # per-ligature rules as allowed positions dictate
...

# ---------- positional forms ----------
lookup TO_MEDI { sub e by e.medi; sub t_i_o_n by t_i_o_n.medi; ... } TO_MEDI;
lookup TO_FINA { sub a by a.fina; sub e by e.fina; sub s by s.fina; sub i_n_g by i_n_g.fina; ... } TO_FINA;
lookup TO_INIT { sub a by a.init; sub T_h by T_h.init; ... } TO_INIT;
lookup TO_ISOL { sub I by I.isol; sub a by a.isol; ... } TO_ISOL;
lookup POS {
  sub @LETTER @BASE' lookup TO_MEDI @LETTER;
  sub @LETTER @BASE' lookup TO_FINA;
  sub @BASE' lookup TO_INIT @LETTER;
  sub @BASE' lookup TO_ISOL;
} POS;

# ---------- variant cycling (ring of 7 states; padded rings repeat modulo their size) ----------
lookup STEP1 { sub a by a.alt1; sub a.init by a.init.alt1; sub b by b.alt1; ... sub z by z.alt1; sub f_i by f_i.alt1; ... } STEP1;
lookup STEP2 { sub a by a.alt2; sub a.init by a.init.alt2; sub b by b.alt2; ... sub f_i by f_i.alt2; ... } STEP2;   # z omitted: slot 2 = z
lookup STEP3 { ... sub z by z.alt1; sub f_i by f_i.alt3; ... } STEP3;
lookup STEP4 { ... } STEP4;  lookup STEP5 { ... } STEP5;  lookup STEP6 { ... } STEP6;
lookup CYCLE {
  sub @ST0 @ST0' lookup STEP1;
  sub @ST0 @TRANSPARENT @ST0' lookup STEP1;
  sub @ST0 @TRANSPARENT @TRANSPARENT @ST0' lookup STEP1;
  sub @ST1 @ST0' lookup STEP2;
  sub @ST1 @TRANSPARENT @ST0' lookup STEP2;
  sub @ST1 @TRANSPARENT @TRANSPARENT @ST0' lookup STEP2;
  sub @ST2 @ST0' lookup STEP3;  sub @ST2 @TRANSPARENT @ST0' lookup STEP3;  sub @ST2 @TRANSPARENT @TRANSPARENT @ST0' lookup STEP3;
  sub @ST3 @ST0' lookup STEP4;  sub @ST3 @TRANSPARENT @ST0' lookup STEP4;  sub @ST3 @TRANSPARENT @TRANSPARENT @ST0' lookup STEP4;
  sub @ST4 @ST0' lookup STEP5;  sub @ST4 @TRANSPARENT @ST0' lookup STEP5;  sub @ST4 @TRANSPARENT @TRANSPARENT @ST0' lookup STEP5;
  sub @ST5 @ST0' lookup STEP6;  sub @ST5 @TRANSPARENT @ST0' lookup STEP6;  sub @ST5 @TRANSPARENT @TRANSPARENT @ST0' lookup STEP6;
} CYCLE;

# ---------- feature registration (LookupList order already fixed by definition order above) ----------
feature liga { lookup LIGA_4; lookup LIGA_4_o_u_g_h; lookup LIGA_4_t_i_o_n; lookup LIGA_3; lookup LIGA_3_S_c_h; ...
               lookup LIGA_2; lookup LIGA_2_C_h; ... } liga;
feature clig { <the same lookup list as liga> } clig;
feature calt { lookup POS; lookup CYCLE; } calt;
feature aalt {
  sub a from [a.alt1 a.alt2 a.alt3 a.alt4 a.alt5 a.alt6 a.init a.init.alt1 a.init.alt2 a.fina];
  sub f_i from [f_i.alt1 f_i.alt2 f_i.alt3];
  ...
} aalt;

table GDEF {
  GlyphClassDef [a a.alt1 ... space period ...], [f_i f_i.alt1 ... t_i_o_n ... S_c_h.alt1], , ;
  LigatureCaretByPos f_i 310;
  LigatureCaretByPos t_i_o_n 240 470 690;
  ...
} GDEF;
```

Size: ≤ 53 ligature chain lookups (+ anonymous ligature lookups), 5 positional, 7 cycling; ≈ 300 subtables; the stub font's whole GSUB was a few KB. Compiled via `FontBuilder.addOpenTypeFeatures(fea_text)` (= `fontTools.feaLib.builder.addOpenTypeFeaturesFromString`); the text is also written next to the font as `<Family>.fea`.

### 9.9 Verified shaping behaviour (HarfBuzz on stub fonts built from this skeleton)

Glyph names are shown after stripping `.alt<k>`/`.<pos>` where the row is about ligatures.

| input | glyphs | shows |
|---|---|---|
| `aaaaaaaa` | `a.init a.alt1 a.alt2 a.alt3 a.alt4 a.alt5 a.alt6 a.fina` | cycling composes with positional forms; transparent `a.fina` not cycled |
| `abab` | `a.init b.alt1 a.alt2 b.alt3` | counter is per stream, not per letter |
| `the the the` | `t h.alt1 e.fina.alt2 / t.alt3 h.alt4 e.fina.alt1 / t.alt2 h.alt3 e.fina` | state carried across spaces; repeated words not stamped |
| `a a`, `a. a` | `a ␣ a.alt1`, `a . ␣ a.alt1` | skip depth 1 and 2 |
| `a...a` | `a . . . a` | depth-3 reset (accepted) |
| `zzzz` | `z z.alt1 z z.alt1` | modulus padding of a 2-variant ring |
| `ffi`, `fi`, `str`, `st` | `f_f_i`, `f_i`, `s_t r` (s_t_r final-only), `s_t` | longest-first; restriction |
| `string` | `s_t r i_n_g` | tiers: blocked `s_t_r` does not block `s_t` |
| `office`, `affix` | `o f f_i c e`, `a f f_i x` | restricted `ff` (own lookup) does not shadow `fi` |
| `asthma`, `Bingham` | `a s t_h m a`, `B i n g_h a m` | restricted `st`/`ng` do not shadow `th`/`gh` |
| `angha` / `agha` | `a n g_h a` / `a g_h a` | restricted `ng` ignored, unconditional `gh` fires |
| `staff`, `best`, `stiff` | `s t a f_f`, `b e s_t`, `s t i f_f` | restricted ligatures still fire in their allowed positions |
| `nation` / `nations` | `n a.alt1 t_i_o_n` / no ligature | final-only cluster |
| `sing` / `singer` | `s i_n_g.alt1` / no ligature | ligature glyph cycles |
| `a` (label with only an `.init` ring) | `a` | isolated letter never takes `.init` |
| `liga` off | `f_i` | `clig` still forms the ligature — the reason both features are registered |
| `liga` + `clig` off | `f f.alt1 i.alt2` | components still cycle |
| `calt` off | `a a a` | static, legible fallback |
| `calt` + `liga` + `clig` off | base glyphs only | the "all three features off" rendering (what Word shows with alternates off) |

These rows become the first failing `test_shaping` cases.

### 9.10 Engine notes and boundaries

- **Run boundaries:** shapers never see across a font/style change or a paragraph; the counter restarts at state 0 there. Harmless.
- **Word-shaping caches:** Gecko's `gfxFont::SpaceMayParticipateInShaping` disables its per-word cache when the font's GSUB references the space glyph, which this font does via `@TRANSPARENT`, so Firefox is expected to shape across spaces. Blink's `CachingWordShaper` shapes space-delimited words separately unless the CSS run explicitly enables kerning/ligature typesetting features (`font-kerning: normal`, `font-variant-ligatures`, `font-feature-settings`); its behaviour on a default-styled page is **not established offline** (§Known risks 4).
- **No-shaping consumers** (Word with alternates off, some PDF viewers' text re-rendering, old editors) see base glyphs = the medoid of each letter's medial ring — still a clean handwriting font; the proof sheet shows this row.
- **Copy/paste and PDF text:** alternates and ligatures carry AGL-decomposable names and `cmap` is base-only, so extracted text is the typed text.

## Build (`build.py`)

Pure: `build_font(plan, outlines, hmetrics, vmetrics, fea_text, meta) -> bytes` (CFF) and `build_ttf(plan, outlines, hmetrics, vmetrics, fea_text, meta) -> bytes`.

```python
ts = timestampSinceEpoch(datetime.fromisoformat(meta.created_at).timestamp())   # fontTools.misc.timeTools: Unix → seconds since 1904-01-01
fb = FontBuilder(1000, isTTF=False)
fb.setupGlyphOrder(order)                       # .notdef, space, then plan order (§6)
fb.setupCharacterMap(cmap)
fb.setupCFF(ps_name, {"FullName": full, "FamilyName": family, "Weight": "Regular", "ItalicAngle": 0,
                      "version": "1.000"}, charstrings, {"nominalWidthX": 0, "defaultWidthX": 0})
fb.setupHorizontalMetrics({name: (advance, lsb)})
fb.setupHorizontalHeader(ascent=asc, descent=desc)
fb.setupNameTable({"familyName": family, "styleName": "Regular", "uniqueFontIdentifier": f"{ps_name};font-gen 0.1.0;{digest12}",
                   "version": "Version 1.000;font-gen 0.1.0;store " + digest12, "psName": ps_name})
fb.setupOS2(sTypoAscender=asc, sTypoDescender=desc, sTypoLineGap=0, usWinAscent=asc, usWinDescent=-desc,
            sxHeight=xh, sCapHeight=ch, fsSelection=0x40 | 0x80, achVendID="HWFT", fsType=0, usWeightClass=400)
fb.setupPost()                                  # format 3 for CFF; names live in the CFF charset
fb.setupHead(created=ts, modified=ts)           # FontBuilder uses TTFont(recalcTimestamp=False), so the value survives save (verified)
fb.addOpenTypeFeatures(fea_text)
fb.save(buf)                                    # BytesIO; the self-check runs on the bytes before anything touches disk
```

Charstrings: `T2CharStringPen(advance, None)` fed by `Outline.draw`. **Width encoding, stated plainly:** `T2CharStringPen.getCharString()` always inserts `otRound(width)` as a raw width operand and does not subtract `nominalWidthX` (verified), so every charstring carries an explicit width and `defaultWidthX` never applies — there is no byte saving to be had, and a nonzero `nominalWidthX` would silently shift every advance. Hence `nominalWidthX = 0`, `defaultWidthX = 0`, always; `test_build` asserts both.

**`.ttf` (`--ttf`)** needs its own builder — `setupGlyf`/`setupMaxp` assert `isTTF` (verified):

```python
fb = FontBuilder(1000, isTTF=True)
fb.setupGlyphOrder(order); fb.setupCharacterMap(cmap)
fb.setupGlyf({name: tt_glyph})                  # Cu2QuPen(TTGlyphPen(None), max_err=1.0, reverse_direction=True): clockwise outers
fb.setupHorizontalMetrics({name: (advance, tt_lsb)})   # lsb recomputed from the TT glyph's xMin
fb.setupHorizontalHeader(ascent=asc, descent=desc)
fb.setupNameTable(...same names...); fb.setupOS2(...same values...)
fb.setupPost()                                  # keepGlyphNames=True is the default; with isTTF this yields post format 2.0 (verified)
fb.setupHead(created=ts, modified=ts)
fb.setupMaxp()
fb.addOpenTypeFeatures(fea_text)                # the same .fea text
fb.save(buf)
```

The TTF goes through the same self-check (§11) with the sign of the largest contour's area flipped; `test_build` asserts `post.formatType == 2.0` for the `.ttf` and `3.0` for the `.otf`.

## QA and error handling (`qa.py`, `run.py`)

Rule: **flag, never silently drop.** The report is always written (`<Family>.qa.json`, sorted keys) and summarised on the console like `ingest-segment`'s candidate listing (status line, counts per severity, one line per warn/error).

**Output-path discipline (`run.py`).**
1. Before loading the store, every output path (`.otf`, `.ttf`, `.fea`, `.qa.json`, proof `.svg`/`.png`) is checked; any that exists without `--force` → `output_exists` (error, exit 1) and **nothing is read or written** — the QA report for this case goes to the console only, since its own path may be the one that exists.
2. Nothing touches disk until the font bytes have passed the self-check. Files are then written in the order `.fea`, `.otf`, `.ttf`, `.qa.json`, `proof.svg`, `proof.png`, each path appended to `written_this_run` after a successful write.
3. **Never delete a file this run did not write.** On any later error (e.g. the `.ttf` failing its self-check after the `.otf` was written) only paths in `written_this_run` are removed; the `.qa.json` is then re-written with the error. With `--force`, a pre-existing file is overwritten only at the moment its new bytes are complete (write to `<path>.tmp` in the same directory, then `os.replace`), so a failed run never leaves a truncated font. `test_qa` plants a pre-existing `.otf` with known bytes, runs without `--force`, and asserts exit 1 and identical bytes; and runs with `--force` and a sabotaged TTF step, asserting the pre-existing file is untouched and no `.tmp` remains.

**Severity policy.**
- **error** — the font is *not* written (or, if the error arises after a write, every file this run wrote is deleted); exit code **1**: `empty_font`, `store_invalid`, `glyph_name_invalid`, `feature_compile_failed`, `reload_failed`, `metrics_inconsistent_binary`, `shaping_check_failed`, `output_exists` (without `--force`).
- **warn** — data problems the owner must know about; the font is written; exit **0**. `--strict` turns any warn into exit **2** and no font: `target_no_samples`, `sample_no_ink`, `sidecar_missing`, `convention_violation`, `kind_mismatch`, `raster_scale_mismatch`, `outline_degenerate`, `outline_empty`, `outline_fit_fallback`, `outline_unstable`, `outline_fragmented`, `raster_fallback`, `raster_unavailable`, `metrics_inconsistent`, `metrics_suspicious`, `advance_outlier`, `x_height_outlier`, `baseline_nonzero` (`|baseline| > 0.1`), `pressure_partial`, `ring_singleton`, `ligature_component_missing`, `proof_png_failed`.
- **info** — `target_below_required`, `ring_short`, `ring_surplus`, `ring_not_prime`, `marginal_used`, `bad_sample_excluded`, `position_rederived`, `ligature_restricted`, `ligature_overlap`, `dot_rendered`, `stroke_degenerate`, `outline_tiny_contour`, `nib_width_assumed`, `space_advance_assumed`, `proof_png_skipped` (cairosvg not installed), font statistics.

**Post-build self-check** (on the bytes, before writing; `uharfbuzz` is a hard dependency so it always runs):
1. `TTFont(BytesIO(otf))` loads; `getGlyphOrder()` equals the plan; charstring count equals glyph count.
2. Every glyph draws into a `RecordingPen`; `BoundsPen` bounds equal `Outline.bounds`; `hmtx[name] == (advance, lsb)`; the largest contour's `AreaPen` area is positive for `.otf` (negative for `.ttf`).
3. `OS/2.sTypoAscender == hhea.ascent` (descender likewise); `usWinAscent ≥` max top; `fsSelection & 0x80`; `head.unitsPerEm == 1000`; `head.created == head.modified == ts`.
4. GSUB `FeatureList` contains `liga`, `clig`, `calt`, `aalt` under both `DFLT` and `latn`; lookup indices increase `LIGA_* → POS → CYCLE`; `liga` and `clig` reference the same lookup indices.
5. **HarfBuzz expectation suite** generated from the plan (`hb.Face(hb.Blob(otf))`, `hb.Font`, `hb.Buffer.add_str`, `guess_segment_properties`, `hb.shape`; names compared after stripping `.alt\d+` and `.(init|medi|fina|isol)` where the expectation says "any form"): every ligature forms in each of its allowed positions and does not form in each disallowed one (probes `x<C>x`, `x<C>`, `<C>x`, `<C>` with `x` a probe letter chosen from a fixed preference list that exists in the font and does not extend any ligature label with its neighbour); for every `ligature_overlap` pair, the competing word shapes to the documented resolution; every label with `≥ 2` eligible samples shapes `gg` (its character doubled; the component string doubled for ligatures) to two distinct glyph names; every positional ring: its probe yields a glyph ending in that `.pos`; the §9.9 fixed rows that apply to the plan; with `{"calt": False, "liga": False, "clig": False}` every cmap'd character shapes to its base glyph; `.notdef` appears in no expectation string.
6. Any failure → `shaping_check_failed` / `metrics_inconsistent_binary` / `reload_failed` (error).

**Hard failures** (exceptions with a clear message, exit 1): store missing (`FileNotFoundError` from `GlyphStore.open`), a sample row failing schema validation, feaLib compile error (`feature_compile_failed`: a bug in `features.py` — typically a rule referencing an omitted lookup — so it must be loud).

## Proof sheet (`proof.py`)

Pillow has no raqm here, so the proof does not ask Pillow to shape. One implementation serves both verbs: `render_proof(font_bytes: bytes, rows: list[tuple[str, str]], missing: list[str]) -> str` (SVG text). It shapes each row with `uharfbuzz`, takes glyph names, `x_advance`/`x_offset`/`y_offset` from the HarfBuzz positions, draws each glyph from the **binary** via `TTFont(BytesIO(font_bytes)).getGlyphSet()[name].draw(SVGPathPen(glyph_set))` at the shaped positions (advances come from the shaper, i.e. from `hmtx`, never from `Outline`), and writes labelled rows with `fill-rule="nonzero"`. Because it reads the binary, `font-gen proof --font Family.otf` works on a bare font with no `Outline`s in hand, and the build's proof is exactly what a shaper sees.

**Default rows** (`build` and `demo`; `proof` without `--only-text`): alphabet upper/lower/digits/punctuation; the full ring of every letter; every ligature inside a real word (from `source_word` when present, else the bare label) in each allowed position; two pangrams; the stamping row (`the the the`, `aaaa eeee llll`, `that there sees`); the positional row (`e eat tee`); the quote row (`'the' don't "the" (the)` — shows the §9.1.5 trade-off); a "features off" row of the first pangram shaped with `calt`, `liga` and `clig` all off (what Word shows with alternates off); and a red-boxed list of `target_no_samples` characters (`build`/`demo` pass it from the plan; `proof` has none). `--text FILE` **appends** one row per non-empty line; `--only-text` replaces the default rows with the file's. SVG is canonical (golden-file tested); `--proof-png` rasterises via `cairosvg` when importable, else `proof_png_skipped` (info) — never a failure of the build. `font-gen demo --out DIR` builds a font from `synth.py` and its proof after a plain `pip install font-gen`.

## CLI (`run.py`, console script `font-gen`)

Subcommands are the one deviation from `ingest-segment`'s flat parser, because there are three verbs with different required arguments:

```
font-gen build --store DIR --out Family.otf [--ttf] [--family "Micah Hand"] [--style Regular]
               [--nib-em 0.045] [--ring 7] [--pos-min 3] [--space-em EM] [--smooth-sigma 0.25]
               [--cycle-mode stream|word] [--proof proof.svg] [--proof-png] [--qa report.json]
               [--strict] [--created-at ISO] [--force]
font-gen demo  --out DIR [--seed 1] [--ttf]
font-gen proof --font Family.otf --out proof.svg [--text FILE] [--only-text] [--png] [--force]
```

`main(argv) -> int` like `ingest_segment.run.main`; `--ring` outside `[2, 13]` or `--pos-min < 2` is an argparse error; refuses to overwrite without `--force` (§11); prints the summary (`Wrote Family.otf: 1,312 glyphs, 48 ligatures, ring 7; QA: 0 errors, 7 warnings, 41 infos → Family.qa.json`). `--created-at` is parsed with `datetime.fromisoformat` (a trailing `Z` accepted; naive values are UTC) and converted as in §10.

## Testing strategy (TDD; plain pytest in `tests/`; synthetic fixtures only)

**Fixtures (`synth.py`, `tests/conftest.py`).** `synth_store(root, spec, seed=1) -> GlyphStore` builds a real `GlyphStore` from a built-in parametric stroke alphabet (`l` stem, `o` loop, `a` loop+stem, `e` loop+bar, `t` stem+cross, `f`, `i` with dot, `n`, `g`, `s`, `r`, capitals `T S`, a seeded generic shape for every other default target; ligatures as joined component strokes) with per-variant seeded jitter (±3 % scale, ±2° shear, ±0.01 em bounce) so variants are measurably different, pressure ramps, optional `width`, rendered rasters per §3 (padded, `S = 256`), half-gap advances, session x-height — written through `GlyphStore.add_sample`/`add_target` with targets shaped like `capture_template.default_targets()` (copied, not imported). `spec` is a dict like `{"a": {"medi": 5, "fina": 3, "init": 1}, "z": {"medi": 2}, "Q": {"isol": 1}, "fi": {"medi": 4}, "ing": {"fina": 3}, "tion": {"fina": 2}, "ff": {"fina": 3}, "st": {"fina": 3}}` plus `raster_only={"q"}`, `bad={...}`, `marginal={...}`, `missing_targets={...}`, `degenerate={...}`, `kind_mismatch={...}`. `synth` is the reference implementation of em-v1: `check_sample_em` returns `[]` for everything it writes, raster included.

- `test_emspace` (in `hwfont-schema` and here): `check_sample_em` accepts synth samples with and without raster bytes; flags y-down strokes, nonzero baseline, bbox/stroke disagreement, mixed pressure; flags a raster whose size disagrees with the padded bbox by > 2 % (via bytes **and** via an explicit `(w, h)` tuple); IHDR parsing of PNG bytes; raster pixel↔em round-trip with the pad.
- `test_load`: shaping position from neighbours (`fox.` → final `x`; `don't` → medial `n`); `position_rederived`; missing sidecar excluded and flagged; `kind_mismatch` for a `single` with a 2-char label and for a sample whose kind differs from its target; digest stable.
- `test_select`: default ring is medial at `≥ 3`, else largest; `POS_MIN`; merge of small positions; medoid slot 0; marginal never slot 0; truncation to `RING_N` with surplus; transparent at `n = 1`; punctuation never positional; every eligible sample maps to exactly one glyph; ligature allowed positions; missing target flagged and unmapped; `target_below_required` uses the eligible count (a target with 12 accepted of which 10 are `bad` is below required) and reports both numbers.
- `test_naming`: AGL names for every default target; ligature underscore names; suffix grammar at `--ring 7` and `--ring 13` (`alt12` valid, `alt13` not); validity regex; longest name ≤ 31; order determinism and uniqueness.
- `test_nib`: tier priority (width > pressure > constant); `w(0) = 0.7·NIB`, `w(1) = 1.3·NIB`; clamps; slope clamp; run splitting at 3 %.
- `test_centerline`: dedupe; dot detection and the stub it produces; a jittered polyline sampled from a known circle smooths to within `0.25·NIB`; near-closed loop detected and closed; resample spacing `8 ± 0.5`; smoothing off reproduces input vertices; Catmull-Rom path is G1 at knots.
- `test_outline`: straight Tier C stroke of length `L` → one contour, area `≈ NIB·L + π(NIB/2)²` within 1 %; a dot → one contour with area within 1 % of `π(NIB/2)²` and no `conicTo` in the recorded path; pressure ramp → measured thickness at the ends `≈ 0.7/1.3·NIB`; union of three widths is one positive-area contour; crossing strokes one contour; `o` → two contours, outer CCW; no line segment longer than 2 units on a smooth curved stroke (facet check); `Qu2CuPen` error bound; the self-intersection test rejects a deliberately corrupted outline (a 1-unit crossing loop) that the area/contour-count heuristic alone passes, and the elevation fallback is byte-equal to exact elevation; the simplify fixed point converges on a rounded figure-eight; degeneracy flags; `Outline.draw` round-trips through `RecordingPen` with explicit starts; determinism.
- `test_autotrace`: synthetic PNG `o` → two contours georeferenced inside the padded bbox within 1 px/S; scikit-image absent (monkeypatched) → `raster_unavailable`; scale mismatch → excluded.
- `test_metrics`: advance rounding; LSB equals `xMin` (negative allowed); space rule and clamp; vertical metrics from extremes and floors; caret positions proportional; outlier flags; `metrics_inconsistent` not raised for a `baseline = 0.05` sample whose bbox is consistent after translation.
- `test_features`: golden `.fea` for the fixture plan; class invariants (every alphabetic form in `@LETTER`, `@STk` built by modulus, transparent excluded from `@ST*`); tier ordering; unconditional ligatures in the shared tier lookup, each restricted ligature in its own lookup placed after it; per-ligature rule shapes for every allowed-position subset; **empty `TO_*`/`STEP*` omitted with their rules** (a plan with no positional rings and a plan where every ring has `n ≤ 2` both compile); `addOpenTypeFeaturesFromString` compiles; lookup indices increase `LIGA → POS → CYCLE`; features present under both scripts; `--ring 5` emits `@ST0..@ST4`, `STEP1..STEP4`.
- `test_shaping` (uharfbuzz): the §9.9 table on the fixture font, including the `office`/`affix`/`asthma`/`Bingham`/`angha` shadowing rows, the `liga`-off-only row (`f_i` survives via `clig`) and the `liga`+`clig`-off row; `@LETTER` completeness (positional form before a final letter still yields `.fina`); the quote row; `--cycle-mode word`; the self-check's generated suite passing on a good font.
- `test_build`: reload; glyph order; base-only cmap with aliases; `hmtx`; charstring bounds equal `Outline.bounds`; every charstring carries an explicit width and the Private dict has `nominalWidthX == defaultWidthX == 0`; OS/2 values; `head.created` equals the converted `--created-at`; `GDEF` classes and carets; `post` format 3; `.ttf` has `glyf` with clockwise outers, `post` format 2 with the same glyph names, `maxp.numGlyphs`, and shapes identically; byte-identical rebuild with the same `--created-at`.
- `test_qa`: one crafted input per finding code; severity → exit-code mapping; self-check catches a font whose `hmtx` was tampered with and one whose GSUB was stripped, and no file is left on disk; `--strict`; `--force`; **pre-existing output untouched** on `output_exists` and on a post-write failure (§11), and no `.tmp` left behind.
- `test_proof`: SVG golden from the built bytes; one `<path>` per shaped glyph; ligature names present; quote row present; red box for a missing target; `proof --font` on a bare `.otf` (no plan in hand) yields the same SVG as the build's proof; `--text` appends and `--only-text` replaces; the PNG branch is guarded by `pytest.importorskip("cairosvg")`.
- `test_synth`: every default target produced; `check_sample_em` clean with rasters; seeds reproducible.
- `test_run` / `test_end_to_end`: `demo` writes `.otf`, `.fea`, `.qa.json`, `proof.svg` with only the hard dependencies importable (cairosvg and scikit-image monkeypatched absent → `proof_png_skipped`, no `raster_*` finding); `build` on the fixture store (strokes + one raster-only label + one missing target + one degenerate sample + one kind mismatch) → reload → shape → the report's codes are exactly the expected set; overwrite refusal.
- `test_smoke`: `__version__ == "0.1.0"`; re-exports importable; `importlib.metadata` finds `font-gen` → `font_gen` (wheel packaging).

Goldens are regenerated only by an explicit `--update-goldens` flag in `conftest.py`.

## Technology

- **fontTools 4.68** — `FontBuilder` (two builders: `isTTF=False` and `isTTF=True`), `feaLib`, `cffLib`, `ttLib`, `misc.timeTools`, pens (`T2CharStringPen`, `TTGlyphPen`, `BoundsPen`, `AreaPen`, `RecordingPen`, `SVGPathPen`, `Qu2CuPen`, `Cu2QuPen`), `agl`.
- **skia-pathops 0.9** — stroke, conics→quads, union, simplify (verified API: `stroke(width, cap, join, miter_limit)`; `union(paths, out_pen)`; `simplify(fix_winding, keep_starting_points, clockwise)`). `conicTo` is never called (verified defect).
- **numpy** — centerline resampling/smoothing; **Pillow** — raster decode, PNG fixtures.
- **uharfbuzz 0.57** — shaping for the self-check, tests and proof (hard dependency; Pillow has no raqm here).
- **scikit-image** (optional) — raster fallback; **cairosvg** (optional) — PNG proof.
- **hwfont-schema** — Contract Y, `emspace`.
- pytest; synthetic fixtures only.

## Numerical choices (summary)

| Choice | Value | Why |
|---|---|---|
| UPM | 1000 | CFF convention; 1 unit = 0.001 em |
| em | ruled row height of the capture region (70 px) | printed, exact, review-implementable without statistics; x-height ≈ 0.45 em |
| sidebearings | half-gap, negative allowed; word edge = session median (fallback 0.05) | additive advances reproduce page spacing; overlaps kept |
| raster pad | 0.05 em | keeps nib overhang inside the crop |
| `NIB` | 0.045 em; `w = NIB·(0.7 + 0.6p)`; Tier A clamp `[0.4, 2.0]·NIB` | 0.4 mm pen on a 7.87 mm row; visible pressure without blobs |
| `RESAMPLE` | 8 units | below device sampling; keeps nuance |
| `SMOOTH_SIGMA` | 0.25 · NIB | removes 1-px RDP quantisation (14 units), keeps tremor > 0.5 mm |
| dot / loop thresholds | length < 0.5·NIB → stroked stub; endpoints < 1.5·NIB and length > 6·NIB → closed | stubs become discs via round caps; cut loops rejoin |
| `RUN_TOL` | 3 % | ledge ≤ 0.7 unit, below integer rounding |
| `QU2CU_ERR` | 1.0 unit (1.5 for raster) | invisible; 2.0 flattens small loops |
| self-intersection test | simplify of the rounded path changes nothing (0.5-unit tolerance) | exact at shipped precision; elevation and simplify-fixed-point fallbacks |
| drift check | contour count equal, area within 1 % of the quad union | gross-error heuristic only |
| `RING_N` / `POS_MIN` | 7 / 3 (`--ring 2..13`, `--pos-min ≥ 2`) | prime ring, no resonance with short words; 1–2 positional samples would stamp |
| transparent skip depth | 2 | covers `␣` and `.␣`; 3 rules per state |
| ligature allowed positions | any position with ≥ 1 eligible sample | a final-only flourish never appears mid-word |
| ligature lookups | shared per tier for unconditional; one per restricted ligature | an `ignore` can never shadow a neighbouring ligature |
| space | 0.5 × median lowercase advance, clamped `[0.20, 0.40]` em | not captured; handwriting word gaps are wide |
| ascender / descender | ≥ 800 / ≤ −250, else data extremes | flourishes never clip; sane line height on sparse stores |
| QA thresholds | §3 descriptive table; bbox 0.05 em; contours 12; x-height ±25 %; advance 0.3–3.0× median | tuned to catch review errors, not handwriting variance |

## Out of Scope (deferred)

- **Exact tapered-capsule envelope + corner-aware least-squares fitter** for Tiers A/B (§7.7) — the v2 fidelity upgrade once `StrokePoint.width` or `.rm` pressure is flowing; also `--calibrate-nib` from the raster, meaningful only once the page raster is real ink rather than a centerline re-render.
- Kerning (`GPOS` pair adjustments from neighbour context), a measured word gap in Contract Y, cursive entry/exit attachment.
- Neighbour-aware alternates (choose the variant whose `left_neighbor` matches the actual preceding glyph) — the data is in Contract Y; it layers as a lookup between `POS` and `CYCLE`.
- CFF subroutinisation (`cffsubr` not in the venv), hinting, WOFF2/subsetting, variable nib axis (CFF2), chisel nib, ink darkening at overlaps (COLR).
- Stylistic sets for manual control (`ss01` no cycling, `ss02` no clusters), `locl`; a `quotedbl`-aware quote-pairing rule that would keep `'the'` word-external.
- Review-tool integration (`review` calling `font-gen` on the live store).

## Known Risks

1. **em-v1 is a promise `review` has not yet kept.** Mitigation: the convention lives in `hwfont_schema.emspace` with an executable validator covering strokes and raster size; `synth.py` is built to the same rules, so the first real store is checked by the same code.
2. **Real stroke data has not been seen** by this module; sampling density, pressure range and stroke fragmentation are assumptions isolated in `nib.py`/`centerline.py` with thresholds exposed as parameters. Today's input is constant-width (`pressure = None`, no `width`), so every glyph ships at `NIB` until the upstream follow-up lands; the design degrades gracefully (Tier C) and says so (`nib_width_assumed`).
3. **Upstream skeletonisation drops branches** (a one-stroke `t` crossbar) and RDP coarsens curves; smoothing recovers curvature but cannot invent missing ink.
4. **Cross-space cycling depends on each host's word-shaping cache, not only on GSUB.** Three hosts need a manual check (`docs/` gets the checklist: `the the the`, `aaaa`, `nation`, `ffi`, `office`, with and without `font-feature-settings: "calt"` / `font-kerning: normal` in CSS):
   - **Blink** (Chrome, Edge, Electron) — its `CachingWordShaper` shapes space-delimited words independently on default-styled text (the reason kerning/contextual rules involving space are known not to apply in Chrome); whether an explicit CSS feature setting switches it to whole-run shaping for this font is not established offline.
   - **Gecko** (Firefox) — `SpaceMayParticipateInShaping` disables its word cache when the font's GSUB references the space glyph, which this font does via `@TRANSPARENT`; expected to work, unverified.
   - **DirectWrite** (Word, WPF, UWP/WinUI) — whether `calt` chains see across a space cannot be established offline; contextual alternates are assumed off in Word by default regardless.
   If a host breaks runs at spaces, `--cycle-mode word` is the fallback (repeated words then render identically in that host only); the base-glyph rendering is designed to stand on its own.
5. **Positional restriction can suppress ligatures** until the store has samples in the needed positions; the report lists every restricted ligature so the owner can capture more words.
6. **Tokenisation mismatch** between `ingest_segment.derive_context` (whitespace words) and the font's boundary set is neutralised by re-deriving positions from neighbours (§5.5); the stored `position_in_word` remains slightly off for punctuation-adjacent letters and is reported, with the proper fix upstream in `_unit_map`.
7. **Straight-quote quotation marks are word-internal** (§9.1.5): `'the'` renders with medial `t`/`e` and the quotes step the counter. Accepted for the apostrophe's sake; visible on the proof sheet; a quote-pairing rule is deferred.
8. **Large stores:** ≈ 5 000 glyphs × ≈ 0.6 KB charstrings ≈ 3 MB `.otf` without subroutinisation; acceptable on the desktop, not for the web (subsetting deferred). Build time is expected in seconds (≈ 4 ms union per glyph); `test_end_to_end` carries a loose budget.
9. **Sub-unit run ledges** in Tier A/B outlines (§7.4) are real geometry below the rounding threshold; if real pressure data shows them, `RUN_TOL` halves or the §7.7 upgrade lands.
10. **Ligature precedence within a tier** is a convention (unconditional before restricted, leftmost first), not a linguistic judgement; `ligature_overlap` lists every competing pair so surprising resolutions can be corrected by capturing the losing cluster in more positions.
## Amendments (2026-11-13, from implementation-plan review; normative, override the sections they name)

Each item below was verified in the repo venv (fontTools 4.68.0, skia-pathops 0.9.2, uharfbuzz 0.57.0) during plan review and supersedes the earlier text.

1. **§7.5 steps 3–4 — self-intersection test.** The "`simplify` of the rounded path changes nothing" oracle is withdrawn: skia-pathops 0.9.2 re-segments clean outlines (a stroked half-circle returns 16 segments instead of 15, a looped `a` 118 instead of 75) and `simplify` is not idempotent on a 150-vertex polygon, so that test flags almost every glyph. The shipped test is a **proper segment-crossing test on the flattened rounded outline**: every cubic is replaced by `FLATTEN_N = 8` chords, vertices closer than `INTERSECT_TOL = 0.5` unit are merged, and the outline is intersection-free iff no two chords cross properly (strictly signed orientation tests; chords consecutive in a contour or sharing an endpoint are never compared; touching and collinear overlap do not count). Exact at integer precision for crossings; pinched contours are sub-unit artefacts, not failures. The fallback order (exact elevation, then the simplify fixed point up to 3 rounds, then `outline_unstable`) is unchanged.
2. **§7.5 drift and area tolerances.** `DRIFT_TOL = 0.05` (5 %), not 1 %: ordinary glyphs drift 2.3 % between the rounded cubics and the quad union. The 1 % area figures in §7.5/§14 were measured before rounding; at the shipped stage `Qu2CuPen(max_err=1)` + integer rounding lose ≈ 2.6 % on a 45-unit stroke and ≈ 4.5 % on a dot, so area assertions use a 5 % relative tolerance.
3. **§7.5 `outline_empty` semantics.** `outline_empty` is reserved for a union that collapses to nothing *after* at least one stroke produced a piece. A glyph whose every stroke is degenerate reports only `stroke_degenerate` + `outline_degenerate`, and bounds-based checks (`metrics_suspicious`, `metrics_inconsistent`, advance/overhang) are skipped on an empty outline.
4. **§9.4 / §9.8 — `POS` guard rows.** When a `TO_*` lookup does not exist, its `POS` row is not dropped but replaced by an `ignore` guard with the same context (medial `ignore sub @LETTER @BASE' @LETTER;`, final `ignore sub @LETTER @BASE';`, initial `ignore sub @BASE' @LETTER;`; isolated needs none). Without the guard the four contexts stop being disjoint: with `TO_INIT`/`TO_FINA` but no `TO_MEDI`, `aaa → a.init a.fina a.fina`; with it `aaa → a.init a.alt1 a.fina.alt2`. The whole `POS` lookup is omitted only when no `TO_*` exists. Note `TO_MEDI` can never exist under §6.3 (medial at `≥ POS_MIN` is always the default ring), so the medial row is always the guard and the skeleton's `e.medi` is unreachable.
5. **§12 / §14 — `proof --font` on a bare `.otf`.** It reproduces every default row except the ligature rows (which need `source_word`s that live only in the store): its SVG is the build's SVG minus those rows, not byte-equal. `test_run` asserts exactly that.
6. **§16 table.** "self-intersection test" row reads: proper chord crossings on the flattened rounded outline (`FLATTEN_N = 8`, `INTERSECT_TOL = 0.5`); drift row reads `5 %`.
7. **Arithmetic forms chosen for exact floats.** `w(p) = nib · (70 + 60 · p) / 100`; resample point counts are half-up (`floor(L / step + 0.5)`), never banker's rounding.
8. **§8 `advance_outlier` — median classes** (polish wave, 2026-11-14; overrides §8 "advance outside `[0.3, 3.0] ×` median advance of the same kind"). Sampled glyphs are grouped into three classes, each with its own median advance: `ligature` (kind `ligature`), `single_alnum` (a single whose label is `str.isalnum()`) and `single_other` (every other single: punctuation, symbols). A sample warns `advance_outlier` when its advance lies outside `[0.3, 3.0] ×` its own class median; the message names the class (`… x the single_alnum median 0.533`) and `data` carries `class`. A class with fewer than 3 sampled glyphs (`ADVANCE_CLASS_MIN`) gets no `advance_outlier` check at all — too few to define an outlier. Mixing letters and punctuation in one median made every real store warn for `.`, `,`, `'` (a period at 0.13 em against a single median of 0.53), so `--strict` refused valid fonts.

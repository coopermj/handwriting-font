# Capture Template Handwriting Row-Width — Module Design

**Date:** 2026-06-30
**Status:** Approved for planning
**Module:** `capture-template` (`generate.py` default config + `planner.py`)
**Depends on:** `hwfont-schema` (unchanged)
**Related:** [ingest-segment](2026-06-18-ingest-segment-design.md), [reMarkable adapter](2026-06-30-remarkable-export-adapter-design.md) — a real capture surfaced this problem.

## Problem

The capture template prompts are **too wide for handwriting to track**. `max_line_chars` defaults to 80, derived from the *printed* prompt-font width:

```python
max_line_chars=80,  # ~ (1404 - 160) / (28 * 0.55)
```

But the writer copies each prompt in the ruled writing area below it (`line_height_px = 70` px tall, `width - 2·margin = 1244` px wide). A handwritten glyph advance is roughly 40–48 px, so only ~26–31 glyphs fit across the row — not 80. A real reMarkable capture confirmed this: page-1 rows carried 60–67 units each; the writer's ink wrapped or ran off the row, breaking the 1:1 prompt-row ↔ handwritten-line correspondence that `ingest-segment` relies on, which in turn drove a flood of low-confidence `needs_review` candidates during segmentation.

Two coupled defects:
1. **Width:** `max_line_chars` is sized for the wrong font (print, not handwriting).
2. **Coverage coupling (latent):** naively lowering `max_line_chars` breaks glyph/ligature coverage. Measured: at `max_line_chars=28` with the default page target, the planner produces 66 lines and coverage drops to **66/121 (unmet)**. Root cause below.

## Root cause of the coverage coupling

In `planner.plan`, `effective_cap = max(line_cap=200, target_lines)` is a **row budget**, and Phase 1 (mandatory coverage) loops:

```python
while rendered < effective_cap and any(d > 0 for d in deficit.values()):
```

`rendered` accumulates each selected sentence's **row cost** (`cost(text) = len(wrap_text(text, max_line_chars))`). Narrower lines make every sentence cost ~3× more rows, so the same row budget covers ~1/3 as many sentences before `rendered` hits `effective_cap` — and Phase 1 stops with the deficit unmet. Coverage, which the code comment already calls "mandatory," is in fact silently bounded by a width-sensitive budget. Empirically, at `max_line_chars=28` with the default page target the planner stops at 66 sentences / 201 rows with coverage 66/121 unmet; only raising the page target to ~36 lets Phase 1 reach full coverage (157 sentences / 365 rows).

## Design Decisions (settled during brainstorming)

1. **Width is a fixed character target**, not derived from row height or physical units: `max_line_chars` default becomes **28** (writing-area advance ≈ 44 px across 1244 px, with right-margin slack). It stays a `PageConfig` field, overridable.
2. **Coverage-first planner** (chosen over "bump the page default" and "auto-scale target in generate"): make Phase 1 meet coverage regardless of the row budget, so line width and booklet length are decoupled. Width becomes a pure ergonomics knob; coverage is guaranteed at any width.
3. **`--pages` becomes a floor for optional variety**, not a coverage constraint.

## Changes

### Change 1 — width default (`generate.py`)

In `_default_config`, set `max_line_chars = 28` and replace the print-font comment with one noting it is sized for handwriting advance in the writing row (writing area ≈ 1244 px wide; handwritten glyph advance ≈ 44 px). No other geometry changes — `line_height_px`, `row_pitch_px`, margins stay as-is (the reported problem is width, not row height).

### Change 2 — coverage-first Phases 1 & 3 (`planner.py`)

Remove the `rendered < effective_cap` guard from the Phase 1 coverage loop so it runs until every target's `required_count` is met or no remaining candidate can reduce the deficit:

```python
# Phase 1 — coverage: meet base counts with genuine sentences. Coverage is
# mandatory and is NOT bounded by effective_cap: narrow rows cost more rows per
# sentence, but every target must still be covered. effective_cap / target_lines
# bound only the Phase 2 variety-fill below.
while any(d > 0 for d in deficit.values()):
    best = None
    for idx, text in pool:
        score = _score(text, targets, deficit)
        if score <= 0:
            continue
        key = (-score, len(text), text, idx)
        if best is None or key < best[0]:
            best = (key, idx, text)
    if best is None:
        break  # genuine pool exhausted; Phase 3 drills fill the remaining deficit
    _, idx, text = best
    pool = [(i, t) for (i, t) in pool if i != idx]
    lines.append(PromptLine(text=text, is_drill=False))
    rendered += cost(text)
    for t in targets:
        occ = count_occurrences(text, t.label)
        achieved_natural[t.label] += occ
        deficit[t.label] = max(0, deficit[t.label] - occ)
```

Phase 2 (variety-fill) is unchanged: it still fills to `fill_limit = max(rendered, min(target_lines, effective_cap - drill_reserve))`, so it only adds sentences when the requested page target exceeds the coverage-driven length (a floor).

**Phase 3 (coverage drills) must also be uncapped.** Phase 3 currently guards each drill with `if rendered >= effective_cap: break`. Once the uncapped Phase 1 has pushed `rendered` past `effective_cap`, those guards would skip Phase 3 entirely, so any target the genuine pool could not fully cover (a glyph/ligature underrepresented in the corpus) would never get its drills — coverage unmet. Remove both `rendered >= effective_cap` breaks from Phase 3 so mandatory drill-fill always clears the remaining deficit:

```python
# Phase 3 — drill-fill remaining base-coverage gaps. Mandatory coverage, so NOT
# bounded by effective_cap (that budget governs only Phase 2 variety-fill).
for t in targets:
    if deficit[t.label] <= 0:
        continue
    for drill_text in _drill_lines(t.label, deficit[t.label], drill_budget):
        lines.append(PromptLine(text=drill_text, is_drill=True))
        rendered += cost(drill_text)
        occ = count_occurrences(drill_text, t.label)
        achieved_drill[t.label] += occ
        deficit[t.label] = max(0, deficit[t.label] - occ)
```

Net rule: mandatory coverage (Phase 1 genuine + Phase 3 drills) is unbounded; optional variety (Phase 2) is bounded by `target_lines`/`effective_cap`.

**Termination:** the deficit is finite (Σ `required_count`) and strictly decreases whenever a scoring candidate or drill is added; when the genuine pool can't reduce it, Phase 1 breaks and Phase 3's synthetic drills (bounded by `drill_budget` per target) cover the remainder. No unbounded loop.

### Semantics & errors

`DEFAULT_PAGES = 12` is unchanged in value but now means a variety floor: `target_lines = pages · rows_per_page` drives Phase 2 only. The `--pages` CLI help is updated to: "target booklet length; the booklet always grows as needed to meet glyph/ligature coverage." `UnmetCoverageError` / `--allow-unmet` remain the safety net (coverage can still be unmet only if drills genuinely cannot satisfy a target), but the default booklet no longer silently under-covers at the narrower width.

## Testing Strategy (TDD)

- **`planner` (core):**
  - **Regression:** coverage is met at a narrow `max_line_chars` (e.g. 28) with a small `target_lines` — fails today (66/121), passes after the fix.
  - **Width-independence:** the count of met targets does not decrease when `max_line_chars` is lowered (same targets/corpus, 80 vs 28 → both fully met).
  - **Variety floor preserved:** with `target_lines` well above the coverage-driven length, Phase 2 still fills toward `target_lines` (booklet grows for variety).
  - **Drill coverage uncapped:** a target that the candidate pool cannot supply (e.g. a glyph absent from the given candidates) is still met via drills at a narrow `max_line_chars` with a small `line_cap`/`target_lines` — proving Phase 3 is no longer starved by the row budget.
- **`generate`:** assert `_default_config().max_line_chars == 28`; an end-to-end `generate()` on a small corpus/targets reports `all_met` at the default config.
- **Existing suite:** all current `planner`/`layout`/`pdf`/`sidecar` tests stay green (they pass explicit configs, unaffected by the default change).

## Out of Scope

- Changing row height / `row_pitch_px` / margins (problem is width, not vertical space).
- Per-writer handwriting-size calibration (a fixed 28-char default is sufficient; the field is overridable).
- Deriving width from row height or physical units (explicitly rejected — fixed target chosen).
- Regenerating sidecars for already-written booklets, or any `ingest-segment` change.

## Known Risk

28 chars is a fixed estimate for "average" handwriting; writers with very large hands may still overrun, and very small hands leave the row underused. It is a `PageConfig` field, so it can be tuned per user without code changes.

**The booklet roughly doubles in length.** Measured against the bundled corpus/targets: meeting coverage takes ~17 pages (184 rows) at `max_line_chars=80` but **~34 pages (365 rows) at 28** — narrower rows hold fewer glyphs, so more rows are needed for the same coverage. This is inherent to the ergonomics fix (readable handwriting needs the space), not incidental, and coverage-first makes the default booklet reach ~34 pages automatically rather than silently under-covering. If a ~34-page booklet is undesirable, the levers are lowering per-target `required_count` or trimming the target set — both out of scope here and independent of this change.

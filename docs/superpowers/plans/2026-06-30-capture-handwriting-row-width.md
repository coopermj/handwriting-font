# Capture Handwriting Row-Width Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make capture-template prompt rows narrow enough to write by hand (`max_line_chars` 80 → 28) without losing glyph/ligature coverage, by decoupling mandatory coverage from the width-sensitive row budget in the planner.

**Architecture:** Two changes to `capture-template`. (1) `generate._default_config` sets `max_line_chars = 28` (handwriting advance, not print font). (2) `planner.plan` runs mandatory coverage — Phase 1 (genuine sentences) and Phase 3 (drills) — until every target's `required_count` is met, regardless of `effective_cap`; only Phase 2 variety-fill stays bounded by `target_lines`. Result: booklet length is driven by coverage at any line width (~34 pages at 28 chars vs ~17 at 80), and `--pages` becomes a variety floor.

**Tech Stack:** Python 3.12, pydantic v2, reportlab, pytest.

**Spec:** [docs/superpowers/specs/2026-06-30-capture-handwriting-row-width-design.md](../specs/2026-06-30-capture-handwriting-row-width-design.md)

---

## Conventions

- Run tests with **`python3`** (no `python` alias): `cd packages/capture-template && python3 -m pytest -q`. If imports fail: `pip install -e packages/hwfont-schema -e packages/capture-template` from the repo root.
- Tests live in `packages/capture-template/tests/`, plain `def test_*`. Match existing `tests/test_planner.py` / `tests/test_generate.py` style.
- `plan(...)` signature (existing): `plan(targets, candidates, line_cap=200, drill_budget=60, target_lines=None, max_line_chars=None) -> PlanResult`. `PlanResult` has `.lines` (list[PromptLine]), `.coverage` (list[CoverageRow]), `.all_met` (bool). `CoverageRow` has `.label`, `.required`, `.achieved`, `.source`, `.met`. `Target(label, kind, required_count)`, `Kind.single`. Import from `hwfont_schema` / `capture_template.planner`.

---

## File Structure

- **Modify:** `packages/capture-template/src/capture_template/planner.py` — uncap Phase 1 coverage loop and Phase 3 drill-fill.
- **Modify:** `packages/capture-template/src/capture_template/generate.py` — `_default_config` `max_line_chars = 28`; `--pages` help text.
- **Test:** `packages/capture-template/tests/test_planner.py` (append coverage-first tests).
- **Test:** `packages/capture-template/tests/test_generate.py` (append default + end-to-end coverage tests).

---

## Task 1: Coverage-first planner (uncap Phase 1 & Phase 3)

**Files:**
- Modify: `packages/capture-template/src/capture_template/planner.py`
- Test: `packages/capture-template/tests/test_planner.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/capture-template/tests/test_planner.py`:

```python
from capture_template.planner import plan
from hwfont_schema import Kind, Target


def _wraps_to_two_rows_with_one_a(i: int) -> str:
    # "a" + 7 filler + space + 8 filler -> exactly one 'a', 16 chars -> 2 rows at mlc=8
    return "a" + chr(98 + i) * 7 + " " + chr(112 + i) * 8


def test_coverage_met_at_narrow_width_despite_small_row_budget():
    # Regression: narrow rows cost more rows/sentence; a small effective_cap must
    # NOT stop mandatory coverage. Pre-fix this yields achieved 'a' == 3 (unmet).
    targets = [Target(label="a", kind=Kind.single, required_count=6)]
    candidates = [_wraps_to_two_rows_with_one_a(i) for i in range(6)]
    result = plan(targets, candidates, line_cap=6, target_lines=6, max_line_chars=8)
    assert result.all_met is True
    row = next(r for r in result.coverage if r.label == "a")
    assert row.achieved >= 6


def test_coverage_is_width_independent():
    # Same targets/candidates/budget at a narrow vs wide width -> both fully covered.
    targets = [Target(label="a", kind=Kind.single, required_count=6)]
    candidates = [_wraps_to_two_rows_with_one_a(i) for i in range(6)]
    narrow = plan(targets, candidates, line_cap=6, target_lines=6, max_line_chars=8)
    wide = plan(targets, candidates, line_cap=6, target_lines=6, max_line_chars=80)
    assert narrow.all_met is True and wide.all_met is True


def test_drill_fill_covers_target_absent_from_pool_uncapped():
    # 'z' appears in no candidate -> only drills can cover it. Small budget must not
    # starve Phase 3. Pre-fix: rendered >= effective_cap skips drills -> unmet.
    targets = [Target(label="z", kind=Kind.single, required_count=4)]
    candidates = ["hello world", "foo bar qux nope", "lorem ipsum dolor"]  # no 'z'
    result = plan(targets, candidates, line_cap=2, target_lines=2, max_line_chars=8)
    row = next(r for r in result.coverage if r.label == "z")
    assert row.met is True
    assert row.source == "drill"


def test_variety_fill_still_honors_target_lines_floor():
    # Coverage needs 1 line; a larger target_lines must still pull in variety sentences.
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    candidates = [f"alpha {chr(98 + i)}{chr(98 + i)}{chr(98 + i)}" for i in range(12)]
    result = plan(targets, candidates, line_cap=200, target_lines=8, max_line_chars=80)
    assert result.all_met is True
    assert len(result.lines) >= 5  # variety-fill grew the booklet toward the target
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/capture-template && python3 -m pytest tests/test_planner.py -q -k "narrow_width or width_independent or drill_fill_covers"`
Expected: FAIL — `test_coverage_met_at_narrow_width_despite_small_row_budget` (achieved 3, all_met False) and `test_drill_fill_covers_target_absent_from_pool_uncapped` (z unmet) fail against the current capped loops. (`test_variety_fill_still_honors_target_lines_floor` may already pass — it guards against regression.)

- [ ] **Step 3: Uncap Phase 1 (coverage loop)**

In `packages/capture-template/src/capture_template/planner.py`, replace the Phase 1 loop header. Change:

```python
    # Phase 1 — coverage: meet base counts with genuine sentences. A selected entry
    # may push `rendered` past effective_cap by its own row-cost; coverage is mandatory.
    while rendered < effective_cap and any(d > 0 for d in deficit.values()):
```

to:

```python
    # Phase 1 — coverage: meet base counts with genuine sentences. Mandatory coverage,
    # so NOT bounded by effective_cap (narrow rows cost more rows/sentence, but every
    # target must still be covered). effective_cap / target_lines bound only Phase 2.
    while any(d > 0 for d in deficit.values()):
```

(The loop body — scoring, `if best is None: break`, appending, `rendered += cost(text)`, deficit update — is unchanged.)

- [ ] **Step 4: Uncap Phase 3 (drill-fill)**

In the same file, replace the Phase 3 block:

```python
    # Phase 3 — drill-fill remaining base-coverage gaps, respecting effective_cap.
    for t in targets:
        if rendered >= effective_cap:
            break
        if deficit[t.label] <= 0:
            continue
        for drill_text in _drill_lines(t.label, deficit[t.label], drill_budget):
            if rendered >= effective_cap:
                break
            lines.append(PromptLine(text=drill_text, is_drill=True))
            rendered += cost(drill_text)
            occ = count_occurrences(drill_text, t.label)
            achieved_drill[t.label] += occ
            deficit[t.label] = max(0, deficit[t.label] - occ)
```

with (both `rendered >= effective_cap` guards removed):

```python
    # Phase 3 — drill-fill remaining base-coverage gaps. Mandatory coverage, so NOT
    # bounded by effective_cap (that budget governs only Phase 2 variety-fill above).
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

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/capture-template && python3 -m pytest tests/test_planner.py -q`
Expected: PASS (the 4 new tests plus all pre-existing planner tests).

- [ ] **Step 6: Commit**

```bash
git add packages/capture-template/src/capture_template/planner.py packages/capture-template/tests/test_planner.py
git commit -m "fix(capture): make coverage width-independent (uncap Phase 1 & drills)"
```

---

## Task 2: Handwriting-width default + `--pages` semantics

**Files:**
- Modify: `packages/capture-template/src/capture_template/generate.py`
- Test: `packages/capture-template/tests/test_generate.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/capture-template/tests/test_generate.py`:

```python
from capture_template.generate import _default_config, generate


def test_default_config_uses_handwriting_line_width():
    assert _default_config().max_line_chars == 28


def test_generate_meets_coverage_at_default_config(tmp_path):
    # Coverage-first + the narrow default must still fully cover all bundled targets.
    result = generate(
        target_spec_path=None,
        corpus_dir=None,
        out_dir=tmp_path / "book",
        force=True,
    )
    assert result.all_met is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/capture-template && python3 -m pytest tests/test_generate.py -q -k "handwriting_line_width or meets_coverage_at_default"`
Expected: FAIL — `test_default_config_uses_handwriting_line_width` fails (default is 80, not 28).

- [ ] **Step 3: Set the handwriting-width default**

In `packages/capture-template/src/capture_template/generate.py`, in `_default_config`, change:

```python
        row_pitch_px=150,
        max_line_chars=80,  # ~ (1404 - 160) / (28 * 0.55)
    )
```

to:

```python
        row_pitch_px=150,
        # sized for handwriting, not the print font: writing area ~1244px wide,
        # handwritten glyph advance ~44px -> ~28 glyphs fit with right-margin slack.
        max_line_chars=28,
    )
```

- [ ] **Step 4: Update the `--pages` help text**

In `main`, change the `--pages` argument's help:

```python
    parser.add_argument(
        "--pages", type=_positive_int, default=DEFAULT_PAGES,
        help=f"target booklet length in pages (default: {DEFAULT_PAGES})",
    )
```

to:

```python
    parser.add_argument(
        "--pages", type=_positive_int, default=DEFAULT_PAGES,
        help=(
            f"variety floor in pages (default: {DEFAULT_PAGES}); the booklet always "
            "grows as needed to meet glyph/ligature coverage"
        ),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/capture-template && python3 -m pytest tests/test_generate.py -q`
Expected: PASS (both new tests plus pre-existing generate tests). `test_generate_meets_coverage_at_default_config` renders a real (~34-page) PDF; it may take a couple of seconds.

- [ ] **Step 6: Commit**

```bash
git add packages/capture-template/src/capture_template/generate.py packages/capture-template/tests/test_generate.py
git commit -m "feat(capture): default prompt rows to handwriting width (28 chars)"
```

---

## Task 3: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run all three package suites**

Run:
```bash
pip install -e packages/hwfont-schema -e packages/capture-template -e packages/ingest-segment
for p in hwfont-schema capture-template ingest-segment; do echo "== $p =="; (cd packages/$p && python3 -m pytest -q 2>&1 | tail -1); done
```
Expected: all green (`ingest-segment` shows 2 env-gated skips). The pre-existing `capture-template` tests that pass explicit `PageConfig`s are unaffected; the ones relying on `_default_config` now see `max_line_chars=28`.

- [ ] **Step 2: Sanity-check the real page count at the new default**

Run:
```bash
cd packages/capture-template && python3 -c "
from capture_template.corpus import default_corpus_paths, load_corpus
from capture_template.targets import default_targets
from capture_template.layout import rows_per_page
from capture_template.generate import _default_config
from capture_template.planner import plan
from capture_template.text_wrap import wrap_text
from hwfont_schema import Kind
cfg=_default_config(); rpp=rows_per_page(cfg)
targets=default_targets(); charset={t.label for t in targets if t.kind==Kind.single}
cands=load_corpus(list(default_corpus_paths()), charset, max_chars=240)
r=plan(targets,cands,target_lines=12*rpp,max_line_chars=cfg.max_line_chars)
rows=sum(len(wrap_text(l.text,cfg.max_line_chars)) for l in r.lines)
print(f'coverage met={r.all_met} | rows={rows} -> ~{-(-rows//rpp)} pages at 28 chars')
"
```
Expected: `coverage met=True` and roughly ~34 pages — confirming the design's measured outcome.

- [ ] **Step 3: Commit (only if a stray fixup was needed)**

```bash
git add -p packages/capture-template   # stage only intended fixups; never `git add -A` at repo root
git commit -m "chore(capture): finalize handwriting row-width change" || echo "nothing to commit"
```

---

## Self-Review notes (checked against the spec)

- **Change 1 — width default** (spec §Change 1): Task 2 sets `max_line_chars=28` + handwriting comment. ✅
- **Change 2 — coverage-first Phases 1 & 3** (spec §Change 2): Task 1 uncaps the Phase 1 `while` (removes `rendered < effective_cap and`) and removes both Phase 3 `rendered >= effective_cap` breaks. ✅
- **`--pages` = variety floor** (spec §Semantics & errors): Task 2 Step 4 updates the help text; `DEFAULT_PAGES` value unchanged. ✅
- **Testing** (spec §Testing Strategy): regression at narrow width (Task 1 test 1), width-independence (test 2), drill-coverage uncapped (test 3), variety floor preserved (test 4), default==28 + end-to-end `all_met` (Task 2 tests). ✅
- **Termination / safety** (spec §Change 2): Phase 1 breaks when no candidate scores; Phase 3 bounded by `drill_budget` and finite deficit — no unbounded loop; existing `UnmetCoverageError`/`--allow-unmet` untouched. ✅
- **Type/name consistency:** tests use the real `plan(...)` signature and `CoverageRow` fields (`.label`, `.achieved`, `.met`, `.source`); `_default_config`/`generate` names match `generate.py`. ✅
- **Out of scope** (spec): no row-height/margin change; no `required_count`/target-set tuning; no `ingest-segment` change. Confirmed none of the tasks touch those. ✅

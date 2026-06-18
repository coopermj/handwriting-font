# capture-template Multi-Line Copy & Cluster-Rich Quotations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let long quotations wrap across consecutive writing-line rows, and add common ligature / `oft` / digraph / trigraph targets (lowercase **and** capital-initial) with curated public-domain quotations so each is captured in genuine context.

**Architecture:** A new pure `wrap_text` drives both the planner's rendered-row accounting and `layout`'s rendering, so a long quote stays one entry through selection but renders as N rows (one sidecar region each) and `--pages` math stays accurate. The default target spec gains a curated cluster set with tiered counts; new bundled corpus files supply the rare clusters.

**Tech Stack:** Python 3.12, hwfont-schema, ReportLab, PyYAML, pytest. No new runtime deps.

---

## File Structure (changes only)

```
packages/capture-template/
  src/capture_template/
    text_wrap.py         # NEW — wrap_text(text, max_line_chars)
    layout.py            # PageConfig.max_line_chars; build_layout wraps entries into rows
    planner.py           # plan() gains max_line_chars; caps count rendered rows
    targets.py           # new cluster targets (lowercase + capital), tiered counts
    generate.py          # MAX_QUOTE_CHARS; wire max_line_chars to plan; _default_config
    data/corpus/
      clusters.txt       # NEW — rare lowercase clusters in composed sentences
      proper_nouns.txt   # NEW — capital-initial clusters via proper nouns
  tests/
    test_text_wrap.py    # NEW
    test_layout.py       # + wrapping/pagination tests
    test_planner.py      # + rendered-row accounting tests
    test_targets.py      # + cluster-target tests
    test_generate.py     # + multi-row rendering test
    test_corpus.py       # + cluster-supply test
```

**Conventions:** `python3` for all commands. Append to every commit message (blank line then the line): `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Commit only the files each task names; no caches.

---

### Task 1: text_wrap module

**Files:**
- Create: `packages/capture-template/src/capture_template/text_wrap.py`
- Test: `packages/capture-template/tests/test_text_wrap.py`

- [ ] **Step 1: Write the failing test** — `tests/test_text_wrap.py`:
```python
from capture_template.text_wrap import wrap_text


def test_wrap_empty_is_empty_list():
    assert wrap_text("", 10) == []
    assert wrap_text("   ", 10) == []


def test_wrap_short_text_is_one_segment():
    assert wrap_text("a cat sat", 20) == ["a cat sat"]


def test_wrap_breaks_on_word_boundaries_within_budget():
    # budget 10: "the quick" (9) fits; adding " brown" (15) doesn't
    assert wrap_text("the quick brown fox", 10) == ["the quick", "brown fox"]


def test_wrap_lone_overlong_word_gets_its_own_line():
    assert wrap_text("a supercalifragilistic b", 6) == ["a", "supercalifragilistic", "b"]


def test_wrap_is_deterministic():
    text = "one two three four five six seven eight"
    assert wrap_text(text, 12) == wrap_text(text, 12)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_text_wrap.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'capture_template.text_wrap'`

- [ ] **Step 3: Write minimal implementation** — `src/capture_template/text_wrap.py`:
```python
from __future__ import annotations


def wrap_text(text: str, max_line_chars: int) -> list[str]:
    """Greedy word-wrap `text` into lines of at most `max_line_chars` characters.

    A single word longer than the budget gets its own (over-long) line. Whitespace
    is collapsed to single spaces. Empty / whitespace-only input returns []."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= max_line_chars:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_text_wrap.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/text_wrap.py packages/capture-template/tests/test_text_wrap.py
git commit -m "feat(capture): add text_wrap word-wrap helper"
```

---

### Task 2: layout wraps entries into rows

**Files:**
- Modify: `packages/capture-template/src/capture_template/layout.py`
- Test: `packages/capture-template/tests/test_layout.py` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/test_layout.py`:
```python
def test_long_entry_wraps_into_consecutive_rows():
    cfg = _config(max_line_chars=12)  # _config passes kwargs to PageConfig
    # one entry that wraps to 3 segments at budget 12
    model = build_layout([PromptLine(text="the quick brown fox jumps over", is_drill=False)], _targets(), cfg)
    rows = [r for p in model.pages for r in p.rows]
    assert [r.prompt_text for r in rows] == ["the quick", "brown fox", "jumps over"]
    # each wrapped row is a normal row: its own transcript + units + bbox
    assert rows[0].expected_transcript == "the quick"
    assert rows[0].expected_units == ["t", "h", "e", "q", "u", "i", "c", "k"]


def test_wrapped_rows_paginate_across_pages():
    cfg = _config(max_line_chars=12)  # rows_per_page == 10 for _config
    # 4 entries each wrapping to 3 rows => 12 rows => 2 pages (10 + 2)
    entries = [PromptLine(text="the quick brown fox jumps over", is_drill=False) for _ in range(4)]
    model = build_layout(entries, _targets(), cfg)
    assert [len(p.rows) for p in model.pages] == [10, 2]


def test_layout_rejects_nonpositive_max_line_chars():
    with pytest.raises(ValueError):
        build_layout([PromptLine(text="cat", is_drill=False)], _targets(), _config(max_line_chars=0))
```

NOTE: the existing `_config` helper builds `PageConfig(**base)` with keyword args. Add `max_line_chars=88` to its `base` dict so the helper accepts a `max_line_chars` override and existing tests get a sane default. Locate the `_config` helper near the top of `test_layout.py` and add `max_line_chars=88` to its `base` dictionary.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_layout.py -v`
Expected: FAIL — `PageConfig.__init__() got an unexpected keyword argument 'max_line_chars'`

- [ ] **Step 3: Write minimal implementation** — in `src/capture_template/layout.py`:

Add the import near the top:
```python
from capture_template.text_wrap import wrap_text
```
Add a `max_line_chars` field (with a default so existing constructions keep working) to `PageConfig`:
```python
@dataclass
class PageConfig:
    width_px: int
    height_px: int
    dpi: int
    margin_px: int
    prompt_font_px: int
    prompt_gap_px: int
    line_height_px: int
    row_pitch_px: int
    max_line_chars: int = 88
```
Add a `max_line_chars` guard to `_validate` (after the usable-width check):
```python
    if config.max_line_chars < 1:
        raise ValueError(f"max_line_chars must be >= 1, got {config.max_line_chars}")
```
Replace `build_layout` so it wraps each entry into segments and lays out one Row per segment with a running row counter:
```python
def build_layout(
    lines: list[PromptLine], targets: list[Target], config: PageConfig
) -> LayoutModel:
    _validate(config)
    per_page = rows_per_page(config)
    model = LayoutModel(config=config)
    row_index = 0
    for line in lines:
        for segment in wrap_text(line.text, config.max_line_chars):
            page_index = row_index // per_page
            row_in_page = row_index % per_page
            if row_in_page == 0:
                model.pages.append(LayoutPage(index=page_index))
            row_top = config.margin_px + row_in_page * config.row_pitch_px
            model.pages[page_index].rows.append(_make_row(segment, targets, config, row_top))
            row_index += 1
    return model
```
(Leave `_make_row`, `rows_per_page`, `Row`, `LayoutPage`, `LayoutModel` unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_layout.py -v`
Expected: PASS (existing layout tests + the 3 new ones). Then run the full suite to confirm no regressions: `python3 -m pytest -v`.

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/layout.py packages/capture-template/tests/test_layout.py
git commit -m "feat(capture): wrap long entries into consecutive layout rows"
```

---

### Task 3: planner counts rendered rows

**Files:**
- Modify: `packages/capture-template/src/capture_template/planner.py`
- Test: `packages/capture-template/tests/test_planner.py` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/test_planner.py`:
```python
from capture_template.text_wrap import wrap_text


def test_plan_target_lines_counts_rendered_rows_not_entries():
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    # each candidate wraps to 2 rows at max_line_chars=2 ("aa bb" -> ["aa","bb"])
    candidates = ["aa bb", "aa cc", "aa dd", "aa ee"]
    result = plan(targets, candidates, target_lines=4, max_line_chars=2)
    rendered = sum(len(wrap_text(line.text, 2)) for line in result.lines)
    assert rendered == 4            # rendered rows respect target_lines
    assert len(result.lines) == 2   # two entries, each costing 2 rows
    assert all(not line.is_drill for line in result.lines)


def test_plan_max_line_chars_none_is_one_row_per_entry():
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    # without max_line_chars, behavior is unchanged (each entry costs one row)
    result = plan(targets, ["aa bb cc dd ee ff"], target_lines=1)
    assert len(result.lines) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_planner.py -v`
Expected: FAIL — `plan() got an unexpected keyword argument 'max_line_chars'`

- [ ] **Step 3: Write minimal implementation** — in `planner.py`:

Add the import near the top (after the hwfont_schema import):
```python
from capture_template.text_wrap import wrap_text
```
Replace the entire `plan` function with this version (adds `max_line_chars`, a `cost` helper, and a `rendered` counter that replaces `len(lines)` in every cap comparison; drills cost one row):
```python
def plan(
    targets: list[Target],
    candidates: list[str],
    line_cap: int = 200,
    drill_budget: int = 60,
    target_lines: int | None = None,
    max_line_chars: int | None = None,
) -> PlanResult:
    def cost(text: str) -> int:
        if max_line_chars is None:
            return 1
        return max(1, len(wrap_text(text, max_line_chars)))

    deficit = {t.label: t.required_count for t in targets}
    achieved_natural = {t.label: 0 for t in targets}
    achieved_drill = {t.label: 0 for t in targets}

    effective_cap = max(line_cap, target_lines) if target_lines is not None else line_cap

    pool = list(enumerate(candidates))
    lines: list[PromptLine] = []
    rendered = 0

    # Phase 1 — coverage: meet base counts with genuine sentences.
    while rendered < effective_cap and any(d > 0 for d in deficit.values()):
        best = None
        for idx, text in pool:
            score = _score(text, targets, deficit)
            if score <= 0:
                continue
            key = (-score, len(text), text, idx)
            if best is None or key < best[0]:
                best = (key, idx, text)
        if best is None:
            break
        _, idx, text = best
        pool = [(i, t) for (i, t) in pool if i != idx]
        lines.append(PromptLine(text=text, is_drill=False))
        rendered += cost(text)
        for t in targets:
            occ = count_occurrences(text, t.label)
            achieved_natural[t.label] += occ
            deficit[t.label] = max(0, deficit[t.label] - occ)

    # Phase 2 — variety-fill: add genuine sentences (never drills) up to target_lines,
    # choosing the most context-novel candidate each step (new bigrams, then new words).
    if target_lines is not None:
        drill_reserve = sum(
            len(_drill_lines(t.label, deficit[t.label], drill_budget))
            for t in targets
            if deficit[t.label] > 0
        )
        fill_limit = max(rendered, min(target_lines, effective_cap - drill_reserve))
        seen_bigrams: set[tuple[str, str]] = set()
        seen_words: set[str] = set()
        for line in lines:
            seen_bigrams |= _bigrams(line.text)
            seen_words |= _words(line.text)
        while rendered < fill_limit and pool:
            best = None
            for idx, text in pool:
                key = (
                    -len(_bigrams(text) - seen_bigrams),
                    -len(_words(text) - seen_words),
                    len(text),
                    text,
                    idx,
                )
                if best is None or key < best[0]:
                    best = (key, idx, text)
            _, idx, text = best
            pool = [(i, t) for (i, t) in pool if i != idx]
            lines.append(PromptLine(text=text, is_drill=False))
            rendered += cost(text)
            seen_bigrams |= _bigrams(text)
            seen_words |= _words(text)
            for t in targets:
                occ = count_occurrences(text, t.label)
                achieved_natural[t.label] += occ
                deficit[t.label] = max(0, deficit[t.label] - occ)

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
            rendered += 1
            occ = count_occurrences(drill_text, t.label)
            achieved_drill[t.label] += occ
            deficit[t.label] = max(0, deficit[t.label] - occ)

    coverage = _coverage(targets, achieved_natural, achieved_drill)
    return PlanResult(lines=lines, coverage=coverage, all_met=all(r.met for r in coverage))
```
(Leave `count_occurrences`, `_score`, `_drill_lines`, `_bigrams`, `_words`, `_coverage`, and the dataclasses unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_planner.py -v`
Expected: PASS (existing planner tests stay green because `max_line_chars=None` makes `cost`≡1 and `rendered`≡`len(lines)`; the 2 new tests pass). Then run the full suite: `python3 -m pytest -v`.

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/planner.py packages/capture-template/tests/test_planner.py
git commit -m "feat(capture): count rendered rows (wrapped) in planner caps"
```

---

### Task 4: cluster targets (lowercase + capital, tiered counts)

**Files:**
- Modify: `packages/capture-template/src/capture_template/targets.py`
- Test: `packages/capture-template/tests/test_targets.py` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/test_targets.py`:
```python
def test_default_targets_include_cluster_set_with_tiered_counts():
    by_label = {t.label: t for t in default_targets()}
    # common cluster (count 8)
    assert by_label["th"].kind == Kind.ligature
    assert by_label["th"].required_count == 8
    # rare lowercase (count 4)
    assert by_label["tch"].required_count == 4
    assert by_label["oft"].required_count == 4
    # common capital digraph (count 6)
    assert by_label["Th"].required_count == 6
    # rare capital (count 3)
    assert by_label["Sch"].required_count == 3
    # capital and lowercase are distinct targets
    assert by_label["Th"].kind == Kind.ligature and by_label["th"].kind == Kind.ligature
    assert "Sh" in by_label and "sh" in by_label
    # all labels unique
    labels = [t.label for t in default_targets()]
    assert len(labels) == len(set(labels))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_targets.py -v`
Expected: FAIL — `KeyError: 'th'`

- [ ] **Step 3: Write minimal implementation** — in `targets.py`, extend the ligature list and add tiered cluster counts.

Replace the `DEFAULT_LIGATURES` constant and add the cluster groups + counts beneath it:
```python
DEFAULT_LIGATURES = [
    # existing
    "fi", "fl", "ff", "ffi", "ffl", "eft", "fore", "ough", "tion", "ing",
    # common ligatures + digraphs (count 8)
    "st", "ll", "ss", "ee", "oo", "th", "ch", "sh", "wh", "ck", "ng", "qu", "gh", "ea", "ou",
    # rare lowercase clusters (count 4)
    "ct", "sp", "ph", "oft", "tch", "dge", "igh", "ght", "thr", "str", "nth",
    # common capital digraphs (count 6)
    "Th", "Sh", "Wh", "St",
    # rare capital clusters (count 3)
    "Ch", "Ph", "Sp", "Qu", "Thr", "Str", "Sch", "Shr",
]

# Per-label required counts for clusters that differ from DEFAULT_LIGATURE_COUNT (8).
DEFAULT_CLUSTER_COUNTS: dict[str, int] = {
    **{lab: 4 for lab in ["ct", "sp", "ph", "oft", "tch", "dge", "igh", "ght", "thr", "str", "nth"]},
    **{lab: 6 for lab in ["Th", "Sh", "Wh", "St"]},
    **{lab: 3 for lab in ["Ch", "Ph", "Sp", "Qu", "Thr", "Str", "Sch", "Shr"]},
}
```
Update `default_targets` to pass the cluster counts as overrides:
```python
def default_targets() -> list[Target]:
    return _build(
        DEFAULT_GLYPHS,
        DEFAULT_GLYPH_COUNT,
        DEFAULT_LIGATURES,
        DEFAULT_LIGATURE_COUNT,
        DEFAULT_CLUSTER_COUNTS,
    )
```
(`_build` already applies `overrides.get(lig, ligature_count)`, so commons stay 8. `load_target_spec` is unchanged — user specs still control their own counts.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_targets.py -v`
Expected: PASS. Then full suite `python3 -m pytest -v` — note: the default end-to-end run now has more targets; the new rare clusters not yet in the corpus are met by **drills** (every cluster fits the drill budget), so `all_met` stays True and existing generate tests still pass. Task 6 replaces those drills with genuine prose.

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/targets.py packages/capture-template/tests/test_targets.py
git commit -m "feat(capture): add ligature/digraph/trigraph targets with tiered counts"
```

---

### Task 5: wire wrapping + longer quotations into generate

**Files:**
- Modify: `packages/capture-template/src/capture_template/generate.py`
- Test: `packages/capture-template/tests/test_generate.py` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/test_generate.py`:
```python
def test_generate_wraps_long_quotation_into_multiple_regions(tmp_path):
    from hwfont_schema import CaptureSidecar

    spec = {
        "glyphs": {"count": 1, "include": "abcdefghijklmnopqrstuvwxyz."},
        "ligatures": {"count": 1, "items": []},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    # one long quotation (~150 chars) that must wrap across several lines
    long_quote = (
        "the quick brown fox jumps over the lazy dog while the calm grey cat "
        "watched from the warm window sill and the bright moon rose slowly."
    )
    (corpus_dir / "c.txt").write_text(long_quote + "\n", encoding="utf-8")

    cfg = PageConfig(
        width_px=1000, height_px=1400, dpi=226, margin_px=50,
        prompt_font_px=24, prompt_gap_px=10, line_height_px=60, row_pitch_px=130,
        max_line_chars=40,
    )
    result = generate(
        target_spec_path=spec_path, corpus_dir=corpus_dir, out_dir=tmp_path / "out",
        config=cfg, pages=4,
    )
    # one genuine entry, but it renders as multiple sidecar regions (wrapped rows)
    genuine = [line for line in result.lines if not line.is_drill]
    assert len(genuine) == 1
    sidecar = CaptureSidecar.model_validate_json(
        (tmp_path / "out" / "capture.sidecar.json").read_text(encoding="utf-8")
    )
    total_regions = sum(len(p.regions) for p in sidecar.pages)
    # the long quote alone is ~130 chars at a 40-char budget -> >= 3 wrapped regions
    assert total_regions >= 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_generate.py -k wraps_long -v`
Expected: FAIL — the long quotation is dropped (current `max_chars` default 90) or renders as a single overflowing row, so `total_regions >= 3` is not met.

- [ ] **Step 3: Write minimal implementation** — in `generate.py`:

Add a constant near `DEFAULT_PAGES`:
```python
MAX_QUOTE_CHARS = 240
```
Give the default config a wrap budget (conservative: usable width / ~0.55·font). Replace `_default_config`:
```python
def _default_config() -> PageConfig:
    return PageConfig(
        width_px=1404,
        height_px=1872,
        dpi=226,
        margin_px=80,
        prompt_font_px=28,
        prompt_gap_px=12,
        line_height_px=70,
        row_pitch_px=150,
        max_line_chars=80,  # ~ (1404 - 160) / (28 * 0.55)
    )
```
Raise the corpus length cap and pass the wrap budget to the planner. Change the `load_corpus` call:
```python
        candidates = load_corpus(list(sources), charset, max_chars=MAX_QUOTE_CHARS)
```
and the `plan` call:
```python
        target_lines = pages * rows_per_page(config)
        result = plan(targets, candidates, target_lines=target_lines, max_line_chars=config.max_line_chars)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_generate.py -v`
Expected: PASS (existing generate tests + the new one). Then full suite `python3 -m pytest -v`.

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/generate.py packages/capture-template/tests/test_generate.py
git commit -m "feat(capture): generate longer wrapped quotations via max_line_chars"
```

---

### Task 6: cluster-rich bundled quotations

**Files:**
- Create: `packages/capture-template/src/capture_template/data/corpus/clusters.txt`
- Create: `packages/capture-template/src/capture_template/data/corpus/proper_nouns.txt`
- Test: `packages/capture-template/tests/test_corpus.py` (append)

These two files are composed, utilitarian, public-domain sentences whose job is to supply the rare clusters in genuine word context (lowercase) and capital-initial context (proper nouns). They are original sentences (not attributed quotations) and contain no copyrighted text.

- [ ] **Step 1: Write the failing test** — append to `tests/test_corpus.py`:
```python
def test_bundled_corpus_supplies_rare_clusters_at_required_counts():
    from capture_template.corpus import default_corpus_paths, load_corpus
    from capture_template.planner import count_occurrences
    from capture_template.targets import default_targets
    from hwfont_schema import Kind

    targets = {t.label: t for t in default_targets()}
    charset = {t.label for t in targets.values() if t.kind == Kind.single}
    pool = load_corpus(default_corpus_paths(), charset, max_chars=240)
    blob = " ".join(pool)

    rare = [
        "ct", "sp", "ph", "oft", "tch", "dge", "igh", "ght", "thr", "str", "nth",
        "Ch", "Ph", "Sp", "Qu", "Thr", "Str", "Sch", "Shr",
    ]
    for label in rare:
        got = count_occurrences(blob, label)
        assert got >= targets[label].required_count, f"{label}: {got} < {targets[label].required_count}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_corpus.py -k rare_clusters -v`
Expected: FAIL — the bundled corpus doesn't yet supply the rare clusters at their required counts.

- [ ] **Step 3: Write the implementation** — create the two files with EXACTLY this content.

`src/capture_template/data/corpus/clusters.txt`:
```
Catch the moment, watch it closely, match your effort, and fetch the prize.
A batch of thatched cottages stood where the pitch and the latch had not been touched.
The bridge by the edge held a judge whose badge bore a smudge and a ridge of dust.
We must not begrudge the knowledge that a ledge and a hedge can dodge the sledge.
Bright light at night brought delight, and the knight saw the high sight with might.
The mighty fight for the right light taught us that insight and foresight bring delight.
We often eat soft bread aloft in the loft, for oft the soft warm crust is best.
Oftentimes the soft loft light and the often gentle aloft breeze made us soften.
Through three thorny thickets the thrush thrust, and the thread of thrift held throughout.
Three thrushes thrived in the throng, throwing thrifty threads through the thorn.
The strong stream on the straight street made a strange yet strong stone structure.
A stray strand of straw lay on the strip where the strict strong stream ran straight.
A perfect act of fact and contact made the strict contract correct and exact.
The actor acted with tact, and the pact and the fact of the contract had impact.
The spirit of spring inspired us to inspect the crisp, sparse, spacious landscape.
A wasp on a crisp wisp of grass made the spider grasp and the sparrow gasp.
A telephone photograph of the elephant near the sphere drew emphasis and triumph.
The pharmacy graph and the phrase on the phonograph gave the physician emphasis.
The tenth month brought a plinth to the labyrinth, a tenth of the ninth at length.
A month hence, the tenth plinth in the labyrinth stood a tenth taller than the ninth.
```

`src/capture_template/data/corpus/proper_nouns.txt`:
```
Charles and Charlotte chose China, and Chester and Chicago cheered for Charleston.
In Chelsea, Charles met Chloe and Christopher near the Cherwell and the Chiltern hills.
Philip and Phoebe studied Philosophy and Physics in Philadelphia and in Phoenix.
Phineas of Phrygia and Philippa of Pharos praised the Phoenician port at Pharsalus.
Spain and Sparta sent spices, and Spencer and Spalding spoke of the Spanish sport.
From Speyer to Spoleto, Spartacus and Spencer crossed Spain toward the Spree.
Queen Quinn of Quebec asked Quentin a quiet question about the Quaker on the quay.
In Queensland, Quintus and Quintilla of Quito quelled the quarrel at Quimper.
Three ships left Thrace, and Threadneedle and Thrushwood lay beyond Three Bridges.
Throgmorton of Thrace and Threlkeld of Thurso met where Three Rivers throng the strait.
Strong Strasbourg and Stratford lay on the Strand, a straight strait toward Stromboli.
Stratton and Strachan of Stranraer strolled the Strand past Strasbourg and Stretford.
Schools in Schenectady and Schleswig taught Schubert and Schmidt and Schiller their scales.
Scholars from Schwerin and Schaffhausen praised Schumann and Schopenhauer at the school.
Shrewd Shropshire shrines drew Shrewsbury pilgrims to the Shrine where shrubs grew.
At Shrivenham and Shrewton, Shropshire shepherds shrank from the shroud on the shrine.
Where, when, and why did Whitman and Whitney whisper while Whitfield and Wharton watched?
Whitby and Whitehall, Whitchurch and Wharfedale, all wheeled where the white wharf stood.
```

- [ ] **Step 4: Reinstall (so the new data files ship) and run**

Run: `cd packages/capture-template && pip install -e . && python3 -m pytest tests/test_corpus.py -k rare_clusters -v`
Expected: PASS. (`pip install -e .` re-runs the `force-include` so the new files are packaged; add `--break-system-packages` only if the environment requires it.) Then run the full suite `python3 -m pytest -v` — all green.

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/data/corpus/clusters.txt packages/capture-template/src/capture_template/data/corpus/proper_nouns.txt packages/capture-template/tests/test_corpus.py
git commit -m "feat(capture): add cluster-rich and proper-noun quotation files"
```

---

## Notes for the Implementer

- **Single wrap function, two consumers.** `text_wrap.wrap_text` is imported by both `planner.py` (row-cost accounting) and `layout.py` (rendering). `generate` passes `config.max_line_chars` to `plan`, and `build_layout` reads it off the same `config`, so the planner's row count and the rendered PDF always agree.
- **`max_line_chars=None` preserves old planner behavior.** Every existing planner test calls `plan` without it → `cost`≡1 → `rendered`≡`len(lines)` → identical results.
- **Targets-before-quotations is safe.** After Task 4 the new rare clusters are met by drills (each fits the drill budget), so `all_met` stays True and no test breaks; Task 6 swaps those drills for genuine prose.
- **Case-sensitive matching** (`count_occurrences` uses `str.find`) makes `Th` and `th` independent targets — that's why capital clusters need their own quotations (proper nouns / sentence openers).
- **Corpus files are append-only additions.** Don't edit `literature.txt`, `speeches.txt`, or `literature_extra.txt`. The `data/corpus/*.txt` glob (already in place) picks the new files up automatically.
- **The cluster/proper-noun sentences are composed utilitarian text**, not attributed quotations — original, public-domain, charset-clean, each ≤240 chars so they survive `MAX_QUOTE_CHARS` and wrap cleanly.
```

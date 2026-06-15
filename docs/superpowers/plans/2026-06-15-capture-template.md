# capture-template Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `capture-template`, which generates a handwriting-capture PDF plus a Contract X sidecar whose sentences guarantee coverage of every target glyph and ligature.

**Architecture:** A Python package (`packages/capture-template/`) depending only on `hwfont-schema` (+ ReportLab, PyYAML). A deterministic greedy planner selects sentences from a corpus (classic literature + speeches) and drill-fills rare targets; a single `layout.py` model (pixel/top-left geometry) drives both the ReportLab PDF renderer and the sidecar emitter so they cannot drift.

**Tech Stack:** Python 3.12, hwfont-schema, ReportLab (PDF), PyYAML (config), pypdf (test-only), pytest, hatchling.

---

## File Structure

```
packages/capture-template/
  pyproject.toml
  src/capture_template/
    __init__.py            # public exports
    targets.py             # target-spec config → list[hwfont_schema.Target] + defaults
    corpus.py              # sentence splitting + charset filtering + bundled corpus access
    planner.py             # deterministic coverage planner + drill-fill; PromptLine/CoverageRow/PlanResult
    layout.py              # PageConfig, Row, LayoutModel; pixel/top-left geometry + validation
    pdf.py                 # px→pt + ReportLab renderer
    sidecar_out.py         # LayoutModel → hwfont_schema.CaptureSidecar
    generate.py            # orchestrator + argparse CLI
    data/corpus/           # bundled default plain-text sources
  tests/
    test_smoke.py
    test_targets.py
    test_corpus.py
    test_planner.py
    test_layout.py
    test_pdf.py
    test_sidecar_out.py
    test_generate.py
```

**Conventions:** all geometry is pixel-space, top-left origin, units = pixels at `dpi`; `pdf.py` is the only file that converts to ReportLab points. Use `python3` for all commands. Append this trailer to every commit message (blank line then the line): `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. `hwfont-schema` is installed editable from `packages/hwfont-schema`.

---

### Task 1: Package scaffold

**Files:**
- Create: `packages/capture-template/pyproject.toml`
- Create: `packages/capture-template/src/capture_template/__init__.py`
- Create: `packages/capture-template/tests/test_smoke.py`

- [ ] **Step 1: Write the failing test** — `tests/test_smoke.py`:
```python
def test_package_imports_and_has_version():
    import capture_template

    assert capture_template.__version__ == "0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_smoke.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'capture_template'`

- [ ] **Step 3: Write minimal implementation**

`packages/capture-template/pyproject.toml`:
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "capture-template"
version = "0.1.0"
description = "Handwriting-capture PDF + Contract X sidecar generator."
requires-python = ">=3.12"
dependencies = ["hwfont-schema", "reportlab>=4.0", "pyyaml>=6.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pypdf>=4.0"]

[project.scripts]
capture-template = "capture_template.generate:main"

[tool.hatch.build.targets.wheel]
packages = ["src/capture_template"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`packages/capture-template/src/capture_template/__init__.py`:
```python
__version__ = "0.1.0"
```

- [ ] **Step 4: Install and run**

Run (install hwfont-schema first so the local dependency resolves):
```bash
cd packages/hwfont-schema && pip install -e . && cd ../capture-template && pip install -e ".[dev]" && python3 -m pytest tests/test_smoke.py -v
```
Expected: PASS. If pip reports an externally-managed environment, retry the `pip install` commands with `--break-system-packages`. If `hwfont-schema` cannot be resolved as a named dependency, install it by path first (`pip install -e ../hwfont-schema`) — do not skip verifying the test passes.

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template
git commit -m "feat(capture): scaffold capture-template package"
```

---

### Task 2: Target spec loading and defaults

**Files:**
- Create: `packages/capture-template/src/capture_template/targets.py`
- Test: `packages/capture-template/tests/test_targets.py`

- [ ] **Step 1: Write the failing test** — `tests/test_targets.py`:
```python
import json

import pytest
from hwfont_schema import Kind

from capture_template.targets import default_targets, load_target_spec


def test_default_targets_include_letters_and_ligatures():
    targets = default_targets()
    by_label = {t.label: t for t in targets}
    assert by_label["a"].kind == Kind.single
    assert by_label["a"].required_count == 12
    assert by_label["fi"].kind == Kind.ligature
    assert by_label["fi"].required_count == 8
    # labels are unique
    assert len({t.label for t in targets}) == len(targets)


def test_load_target_spec_builds_targets_with_overrides(tmp_path):
    spec = {
        "glyphs": {"count": 10, "include": "ab"},
        "ligatures": {"count": 5, "items": ["fi", "eft"]},
        "overrides": {"eft": 20},
    }
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    targets = load_target_spec(path)
    by_label = {t.label: t for t in targets}
    assert by_label["a"].required_count == 10
    assert by_label["a"].kind == Kind.single
    assert by_label["fi"].required_count == 5
    assert by_label["eft"].required_count == 20  # override wins
    assert by_label["eft"].kind == Kind.ligature


def test_load_target_spec_rejects_duplicate_label(tmp_path):
    # a single-char glyph and a ligature cannot share a label, and a glyph char
    # cannot repeat in `include`
    spec = {"glyphs": {"count": 1, "include": "aa"}, "ligatures": {"count": 1, "items": []}}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError):
        load_target_spec(path)


def test_load_target_spec_rejects_one_char_ligature(tmp_path):
    spec = {"glyphs": {"count": 1, "include": "a"}, "ligatures": {"count": 1, "items": ["x"]}}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError):
        load_target_spec(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_targets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'capture_template.targets'`

- [ ] **Step 3: Write minimal implementation** — `src/capture_template/targets.py`:
```python
from __future__ import annotations

import json
import string
from pathlib import Path
from typing import Any

import yaml
from hwfont_schema import Kind, Target

DEFAULT_GLYPH_COUNT = 12
DEFAULT_LIGATURE_COUNT = 8
DEFAULT_GLYPHS = string.ascii_letters + string.digits + ".,!?'\"-;:()"
DEFAULT_LIGATURES = ["fi", "fl", "ff", "ffi", "ffl", "eft", "fore", "ough", "tion", "ing"]


def _build(
    glyph_chars: str,
    glyph_count: int,
    ligatures: list[str],
    ligature_count: int,
    overrides: dict[str, int],
) -> list[Target]:
    seen: set[str] = set()
    targets: list[Target] = []

    for ch in glyph_chars:
        if ch in seen:
            raise ValueError(f"duplicate target label: {ch!r}")
        seen.add(ch)
        targets.append(
            Target(label=ch, kind=Kind.single, required_count=overrides.get(ch, glyph_count))
        )

    for lig in ligatures:
        if len(lig) < 2:
            raise ValueError(f"ligature must be at least 2 characters: {lig!r}")
        if lig in seen:
            raise ValueError(f"duplicate target label: {lig!r}")
        seen.add(lig)
        targets.append(
            Target(
                label=lig, kind=Kind.ligature, required_count=overrides.get(lig, ligature_count)
            )
        )

    return targets


def default_targets() -> list[Target]:
    return _build(
        DEFAULT_GLYPHS, DEFAULT_GLYPH_COUNT, DEFAULT_LIGATURES, DEFAULT_LIGATURE_COUNT, {}
    )


def load_target_spec(path: str | Path) -> list[Target]:
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    data: dict[str, Any] = (
        json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
    )
    glyphs = data.get("glyphs", {})
    ligatures = data.get("ligatures", {})
    return _build(
        glyph_chars=glyphs.get("include", DEFAULT_GLYPHS),
        glyph_count=int(glyphs.get("count", DEFAULT_GLYPH_COUNT)),
        ligatures=list(ligatures.get("items", DEFAULT_LIGATURES)),
        ligature_count=int(ligatures.get("count", DEFAULT_LIGATURE_COUNT)),
        overrides={k: int(v) for k, v in data.get("overrides", {}).items()},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_targets.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/targets.py packages/capture-template/tests/test_targets.py
git commit -m "feat(capture): add target spec loading and defaults"
```

---

### Task 3: Corpus loading (sentence split + charset filter)

**Files:**
- Create: `packages/capture-template/src/capture_template/corpus.py`
- Test: `packages/capture-template/tests/test_corpus.py`

- [ ] **Step 1: Write the failing test** — `tests/test_corpus.py`:
```python
import pytest

from capture_template.corpus import load_corpus, sentence_in_charset, split_sentences


def test_split_sentences_splits_on_terminators_and_collapses_whitespace():
    text = "The quick brown fox.  It jumps!\nDoes it?  Yes."
    assert split_sentences(text) == [
        "The quick brown fox.",
        "It jumps!",
        "Does it?",
        "Yes.",
    ]


def test_sentence_in_charset():
    charset = set("abcdefghijklmnopqrstuvwxyz .")
    assert sentence_in_charset("a cat.", charset) is True
    assert sentence_in_charset("a c4t.", charset) is False


def test_load_corpus_filters_by_charset_and_length_and_dedupes(tmp_path):
    charset = set("abcdefghijklmnopqrstuvwxyz .")
    (tmp_path / "a.txt").write_text(
        "the cat sat on the warm mat.\n"  # ok
        "x9 bad chars here now today.\n"  # rejected: digit '9'
        "no.\n"  # rejected: too short
        "the cat sat on the warm mat.\n",  # duplicate of first
        encoding="utf-8",
    )
    got = load_corpus([tmp_path / "a.txt"], charset, min_chars=8, max_chars=90)
    assert got == ["the cat sat on the warm mat."]


def test_load_corpus_raises_when_no_usable_sentences(tmp_path):
    (tmp_path / "a.txt").write_text("123456789!!!\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_corpus([tmp_path / "a.txt"], set("abc ."), min_chars=4, max_chars=90)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_corpus.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'capture_template.corpus'`

- [ ] **Step 3: Write minimal implementation** — `src/capture_template/corpus.py`:
```python
from __future__ import annotations

import re
from pathlib import Path

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE = re.compile(r"\s+")


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for chunk in _SENTENCE_END.split(text):
        cleaned = _WHITESPACE.sub(" ", chunk).strip()
        if cleaned:
            sentences.append(cleaned)
    return sentences


def sentence_in_charset(sentence: str, charset: set[str]) -> bool:
    return all(ch == " " or ch in charset for ch in sentence)


def load_corpus(
    paths: list[Path],
    charset: set[str],
    min_chars: int = 12,
    max_chars: int = 90,
) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        for sentence in split_sentences(text):
            if not (min_chars <= len(sentence) <= max_chars):
                continue
            if not sentence_in_charset(sentence, charset):
                continue
            if sentence in seen:
                continue
            seen.add(sentence)
            result.append(sentence)
    if not result:
        raise ValueError("no usable sentences found in corpus sources")
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_corpus.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/corpus.py packages/capture-template/tests/test_corpus.py
git commit -m "feat(capture): add corpus loading with sentence split and charset filter"
```

---

### Task 4: Occurrence counting

**Files:**
- Create: `packages/capture-template/src/capture_template/planner.py`
- Test: `packages/capture-template/tests/test_planner.py`

- [ ] **Step 1: Write the failing test** — `tests/test_planner.py`:
```python
from capture_template.planner import count_occurrences


def test_count_occurrences_single_char():
    assert count_occurrences("banana", "a") == 3


def test_count_occurrences_non_overlapping_substring():
    # "aa" in "aaaa" is 2 non-overlapping, not 3
    assert count_occurrences("aaaa", "aa") == 2


def test_count_occurrences_ligature():
    assert count_occurrences("left often after", "eft") == 1
    assert count_occurrences("effect", "eft") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_planner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'capture_template.planner'`

- [ ] **Step 3: Write minimal implementation** — `src/capture_template/planner.py`:
```python
from __future__ import annotations


def count_occurrences(text: str, label: str) -> int:
    """Non-overlapping, left-to-right occurrences of `label` in `text`."""
    if not label:
        return 0
    count = 0
    start = 0
    while True:
        idx = text.find(label, start)
        if idx == -1:
            return count
        count += 1
        start = idx + len(label)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_planner.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/planner.py packages/capture-template/tests/test_planner.py
git commit -m "feat(capture): add non-overlapping occurrence counting"
```

---

### Task 5: Greedy natural-sentence planning

**Files:**
- Modify: `packages/capture-template/src/capture_template/planner.py`
- Test: `packages/capture-template/tests/test_planner.py` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/test_planner.py`:
```python
from hwfont_schema import Kind, Target

from capture_template.planner import PlanResult, PromptLine, plan


def test_plan_selects_natural_sentences_to_meet_coverage():
    targets = [Target(label="a", kind=Kind.single, required_count=2)]
    candidates = ["banana bread", "zzz", "a"]
    result = plan(targets, candidates)
    assert isinstance(result, PlanResult)
    assert result.all_met is True
    # the banana sentence alone supplies 3 a's; planner should pick it first and stop
    assert result.lines[0] == PromptLine(text="banana bread", is_drill=False)
    assert all(line.is_drill is False for line in result.lines)


def test_plan_is_deterministic_with_stable_tie_breaking():
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    candidates = ["cat", "bat"]  # both supply exactly one 'a'; tie
    first = plan(targets, candidates).lines
    second = plan(targets, candidates).lines
    assert first == second
    # tie broken by (shorter, then lexicographic, then index): "bat" and "cat" same length -> lexicographic
    assert first[0].text == "bat"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_planner.py -v`
Expected: FAIL with `ImportError: cannot import name 'PlanResult'`

- [ ] **Step 3: Write minimal implementation** — add to `src/capture_template/planner.py` (add the imports at the top, then the dataclasses and `plan`):

Add at the top of the file:
```python
from dataclasses import dataclass, field

from hwfont_schema import Kind, Target
```

Add to the file body:
```python
@dataclass
class PromptLine:
    text: str
    is_drill: bool


@dataclass
class CoverageRow:
    label: str
    kind: Kind
    required: int
    achieved: int
    source: str  # "natural" | "drill" | "none"
    met: bool


@dataclass
class PlanResult:
    lines: list[PromptLine] = field(default_factory=list)
    coverage: list[CoverageRow] = field(default_factory=list)
    all_met: bool = False


def _score(text: str, targets: list[Target], deficit: dict[str, int]) -> int:
    return sum(
        min(count_occurrences(text, t.label), deficit[t.label])
        for t in targets
        if deficit[t.label] > 0
    )


def plan(targets: list[Target], candidates: list[str], line_cap: int = 200) -> PlanResult:
    deficit = {t.label: t.required_count for t in targets}
    achieved_natural = {t.label: 0 for t in targets}

    pool = list(enumerate(candidates))  # (original_index, text)
    lines: list[PromptLine] = []

    while len(lines) < line_cap and any(d > 0 for d in deficit.values()):
        best = None  # (neg_score, length, text, index)
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
        for t in targets:
            occ = count_occurrences(text, t.label)
            achieved_natural[t.label] += occ
            deficit[t.label] = max(0, deficit[t.label] - occ)

    coverage = _coverage(targets, achieved_natural, {t.label: 0 for t in targets})
    return PlanResult(lines=lines, coverage=coverage, all_met=all(r.met for r in coverage))


def _coverage(
    targets: list[Target],
    achieved_natural: dict[str, int],
    achieved_drill: dict[str, int],
) -> list[CoverageRow]:
    rows: list[CoverageRow] = []
    for t in targets:
        nat = achieved_natural[t.label]
        total = nat + achieved_drill[t.label]
        if nat >= t.required_count:
            source = "natural"
        elif total >= t.required_count:
            source = "drill"
        else:
            source = "none"
        rows.append(
            CoverageRow(
                label=t.label,
                kind=t.kind,
                required=t.required_count,
                achieved=total,
                source=source,
                met=total >= t.required_count,
            )
        )
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_planner.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/planner.py packages/capture-template/tests/test_planner.py
git commit -m "feat(capture): add greedy natural-sentence coverage planning"
```

---

### Task 6: Drill-fill for unmet targets

**Files:**
- Modify: `packages/capture-template/src/capture_template/planner.py`
- Test: `packages/capture-template/tests/test_planner.py` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/test_planner.py`:
```python
def test_plan_drill_fills_rare_ligature_absent_from_corpus():
    # corpus has no "eft"; planner must drill-fill it and flag the line as a drill
    targets = [Target(label="eft", kind=Kind.ligature, required_count=3)]
    candidates = ["the cat sat", "a dog ran"]
    result = plan(targets, candidates)
    assert result.all_met is True
    drills = [line for line in result.lines if line.is_drill]
    assert drills, "expected at least one drill line"
    assert "eft" in drills[0].text
    cov = {r.label: r for r in result.coverage}
    assert cov["eft"].source == "drill"
    assert cov["eft"].met is True


def test_plan_reports_unmet_target_when_drill_budget_too_small():
    # a ligature longer than the drill budget can never be placed -> unmet, not dropped
    targets = [Target(label="abcdefgh", kind=Kind.ligature, required_count=1)]
    result = plan(targets, [], drill_budget=4)
    assert result.all_met is False
    cov = {r.label: r for r in result.coverage}
    assert cov["abcdefgh"].met is False
    assert cov["abcdefgh"].source == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_planner.py -v`
Expected: FAIL — `plan() got an unexpected keyword argument 'drill_budget'` and drill assertions fail.

- [ ] **Step 3: Write minimal implementation** — in `src/capture_template/planner.py`, add the `_drill_lines` helper and update `plan` to accept `drill_budget` and run drill-fill after natural selection.

Add this helper to the file:
```python
def _drill_lines(label: str, needed: int, drill_budget: int) -> list[str]:
    """Lines repeating `label` (space-separated), each within `drill_budget` chars.

    Returns [] if a single occurrence cannot fit the budget (label longer than budget)."""
    if len(label) > drill_budget or needed <= 0:
        return []
    per_line = max(1, (drill_budget + 1) // (len(label) + 1))  # tokens that fit "lab lab ..."
    lines: list[str] = []
    remaining = needed
    while remaining > 0:
        take = min(per_line, remaining)
        lines.append(" ".join([label] * take))
        remaining -= take
    return lines
```

Replace the `plan` function with this version (adds the `drill_budget` parameter and the drill-fill phase; the natural-selection loop is unchanged):
```python
def plan(
    targets: list[Target],
    candidates: list[str],
    line_cap: int = 200,
    drill_budget: int = 60,
) -> PlanResult:
    deficit = {t.label: t.required_count for t in targets}
    achieved_natural = {t.label: 0 for t in targets}
    achieved_drill = {t.label: 0 for t in targets}

    pool = list(enumerate(candidates))
    lines: list[PromptLine] = []

    while len(lines) < line_cap and any(d > 0 for d in deficit.values()):
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
        for t in targets:
            occ = count_occurrences(text, t.label)
            achieved_natural[t.label] += occ
            deficit[t.label] = max(0, deficit[t.label] - occ)

    # Drill-fill any target still short. Iterate targets in a stable order.
    for t in targets:
        if deficit[t.label] <= 0:
            continue
        for drill_text in _drill_lines(t.label, deficit[t.label], drill_budget):
            lines.append(PromptLine(text=drill_text, is_drill=True))
            occ = count_occurrences(drill_text, t.label)
            achieved_drill[t.label] += occ
            deficit[t.label] = max(0, deficit[t.label] - occ)

    coverage = _coverage(targets, achieved_natural, achieved_drill)
    return PlanResult(lines=lines, coverage=coverage, all_met=all(r.met for r in coverage))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_planner.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/planner.py packages/capture-template/tests/test_planner.py
git commit -m "feat(capture): add drill-fill and unmet-target reporting"
```

---

### Task 7: Layout geometry

**Files:**
- Create: `packages/capture-template/src/capture_template/layout.py`
- Test: `packages/capture-template/tests/test_layout.py`

- [ ] **Step 1: Write the failing test** — `tests/test_layout.py`:
```python
import pytest
from hwfont_schema import Kind, Target

from capture_template.layout import PageConfig, build_layout, rows_per_page
from capture_template.planner import PromptLine


def _config(**kw) -> PageConfig:
    base = dict(
        width_px=1000,
        height_px=1400,
        dpi=226,
        margin_px=50,
        prompt_font_px=24,
        prompt_gap_px=10,
        line_height_px=60,
        row_pitch_px=130,
    )
    base.update(kw)
    return PageConfig(**base)


def _targets():
    return [
        Target(label="c", kind=Kind.single, required_count=1),
        Target(label="a", kind=Kind.single, required_count=1),
        Target(label="t", kind=Kind.single, required_count=1),
        Target(label="at", kind=Kind.ligature, required_count=1),
    ]


def test_rows_per_page_floor_of_usable_height():
    # usable height = 1400 - 2*50 = 1300; 1300 / 130 = 10
    assert rows_per_page(_config()) == 10


def test_build_layout_geometry_and_pagination():
    lines = [PromptLine(text="cat", is_drill=False) for _ in range(12)]
    model = build_layout(lines, _targets(), _config())
    # 12 rows, 10 per page -> 2 pages (10 + 2)
    assert [len(p.rows) for p in model.pages] == [10, 2]

    row0 = model.pages[0].rows[0]
    # bbox: x=margin, y=margin+prompt_font+gap, w=width-2*margin, h=line_height
    assert (row0.bbox.x, row0.bbox.w, row0.bbox.h) == (50, 900, 60)
    assert row0.bbox.y == 50 + 24 + 10  # 84
    assert row0.baseline_y == row0.bbox.y + 60  # 144, inside [bbox.y, bbox.y+h]
    assert row0.expected_transcript == "cat"
    assert row0.expected_units == ["c", "a", "t"]  # only in-charset glyph labels, in order
    assert row0.ligature_targets == ["at"]

    # second row top advances by row_pitch
    row1 = model.pages[0].rows[1]
    assert row1.bbox.y == 50 + 130 + 24 + 10


def test_build_layout_rejects_row_that_does_not_fit_pitch():
    # prompt_font + gap + line_height = 24 + 10 + 60 = 94 must be <= row_pitch
    bad = _config(row_pitch_px=80)
    with pytest.raises(ValueError):
        build_layout([PromptLine(text="cat", is_drill=False)], _targets(), bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_layout.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'capture_template.layout'`

- [ ] **Step 3: Write minimal implementation** — `src/capture_template/layout.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field

from hwfont_schema import BBox, Kind, Target

from capture_template.planner import PromptLine


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


@dataclass
class Row:
    prompt_text: str
    expected_transcript: str
    expected_units: list[str]
    ligature_targets: list[str]
    bbox: BBox
    baseline_y: float


@dataclass
class LayoutPage:
    index: int
    rows: list[Row] = field(default_factory=list)


@dataclass
class LayoutModel:
    config: PageConfig
    pages: list[LayoutPage] = field(default_factory=list)


def rows_per_page(config: PageConfig) -> int:
    usable = config.height_px - 2 * config.margin_px
    return usable // config.row_pitch_px


def _validate(config: PageConfig) -> None:
    row_content = config.prompt_font_px + config.prompt_gap_px + config.line_height_px
    if row_content > config.row_pitch_px:
        raise ValueError(
            f"row content {row_content}px exceeds row_pitch_px {config.row_pitch_px}px"
        )
    if rows_per_page(config) < 1:
        raise ValueError(
            f"page too short: usable height {config.height_px - 2 * config.margin_px}px "
            f"< row_pitch_px {config.row_pitch_px}px"
        )


def _make_row(text: str, targets: list[Target], config: PageConfig, row_top: int) -> Row:
    glyph_labels = {t.label for t in targets if t.kind == Kind.single}
    ligature_labels = [t.label for t in targets if t.kind == Kind.ligature]
    bbox_y = row_top + config.prompt_font_px + config.prompt_gap_px
    bbox = BBox(
        x=float(config.margin_px),
        y=float(bbox_y),
        w=float(config.width_px - 2 * config.margin_px),
        h=float(config.line_height_px),
    )
    return Row(
        prompt_text=text,
        expected_transcript=text,
        expected_units=[ch for ch in text if ch in glyph_labels],
        ligature_targets=[lig for lig in ligature_labels if lig in text],
        bbox=bbox,
        baseline_y=float(bbox_y + config.line_height_px),
    )


def build_layout(
    lines: list[PromptLine], targets: list[Target], config: PageConfig
) -> LayoutModel:
    _validate(config)
    per_page = rows_per_page(config)
    model = LayoutModel(config=config)
    for line_index, line in enumerate(lines):
        page_index = line_index // per_page
        row_in_page = line_index % per_page
        if row_in_page == 0:
            model.pages.append(LayoutPage(index=page_index))
        row_top = config.margin_px + row_in_page * config.row_pitch_px
        model.pages[page_index].rows.append(_make_row(line.text, targets, config, row_top))
    return model
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_layout.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/layout.py packages/capture-template/tests/test_layout.py
git commit -m "feat(capture): add layout geometry engine"
```

---

### Task 8: Sidecar emitter (Contract X)

**Files:**
- Create: `packages/capture-template/src/capture_template/sidecar_out.py`
- Test: `packages/capture-template/tests/test_sidecar_out.py`

- [ ] **Step 1: Write the failing test** — `tests/test_sidecar_out.py`:
```python
from hwfont_schema import CaptureSidecar, Kind, Target

from capture_template.layout import PageConfig, build_layout
from capture_template.planner import PromptLine
from capture_template.sidecar_out import build_sidecar


def _config() -> PageConfig:
    return PageConfig(
        width_px=1000,
        height_px=1400,
        dpi=226,
        margin_px=50,
        prompt_font_px=24,
        prompt_gap_px=10,
        line_height_px=60,
        row_pitch_px=130,
    )


def _targets():
    return [
        Target(label="c", kind=Kind.single, required_count=1),
        Target(label="a", kind=Kind.single, required_count=1),
        Target(label="t", kind=Kind.single, required_count=1),
        Target(label="at", kind=Kind.ligature, required_count=1),
    ]


def test_build_sidecar_matches_layout_and_validates():
    lines = [PromptLine(text="cat", is_drill=False), PromptLine(text="cat", is_drill=False)]
    model = build_layout(lines, _targets(), _config())
    sidecar = build_sidecar(model)

    # validates via hwfont-schema round-trip
    assert CaptureSidecar.model_validate_json(sidecar.model_dump_json()) == sidecar

    assert len(sidecar.pages) == 1
    page = sidecar.pages[0]
    assert (page.width_px, page.height_px, page.dpi) == (1000, 1400, 226)
    assert page.source_bounds is not None
    assert (page.source_bounds.w, page.source_bounds.h) == (1000, 1400)

    # one region per prompt line, geometry copied from the layout row
    assert len(page.regions) == 2
    region0 = page.regions[0]
    row0 = model.pages[0].rows[0]
    assert region0.expected_transcript == "cat"
    assert region0.expected_units == ["c", "a", "t"]
    assert region0.ligature_targets == ["at"]
    assert region0.baseline_y == row0.baseline_y
    assert region0.bbox == row0.bbox


def test_region_ids_are_unique():
    lines = [PromptLine(text="cat", is_drill=False) for _ in range(15)]  # spans 2 pages
    sidecar = build_sidecar(build_layout(lines, _targets(), _config()))
    ids = [r.id for p in sidecar.pages for r in p.regions]
    assert len(ids) == len(set(ids)) == 15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_sidecar_out.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'capture_template.sidecar_out'`

- [ ] **Step 3: Write minimal implementation** — `src/capture_template/sidecar_out.py`:
```python
from __future__ import annotations

from hwfont_schema import BBox, CaptureSidecar, Page, Region

from capture_template.layout import LayoutModel


def build_sidecar(model: LayoutModel) -> CaptureSidecar:
    cfg = model.config
    pages: list[Page] = []
    for page in model.pages:
        regions: list[Region] = []
        for row_index, row in enumerate(page.rows):
            regions.append(
                Region(
                    id=f"p{page.index}-r{row_index}",
                    expected_transcript=row.expected_transcript,
                    baseline_y=row.baseline_y,
                    bbox=row.bbox,
                    expected_units=row.expected_units,
                    ligature_targets=row.ligature_targets,
                )
            )
        pages.append(
            Page(
                id=f"p{page.index}",
                width_px=cfg.width_px,
                height_px=cfg.height_px,
                dpi=cfg.dpi,
                source_bounds=BBox(x=0.0, y=0.0, w=float(cfg.width_px), h=float(cfg.height_px)),
                regions=regions,
            )
        )
    return CaptureSidecar(pages=pages)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_sidecar_out.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/sidecar_out.py packages/capture-template/tests/test_sidecar_out.py
git commit -m "feat(capture): add Contract X sidecar emitter"
```

---

### Task 9: PDF renderer

**Files:**
- Create: `packages/capture-template/src/capture_template/pdf.py`
- Test: `packages/capture-template/tests/test_pdf.py`

- [ ] **Step 1: Write the failing test** — `tests/test_pdf.py`:
```python
import pytest
from hwfont_schema import Kind, Target
from pypdf import PdfReader

from capture_template.layout import PageConfig, build_layout
from capture_template.pdf import px_to_pt, render_pdf
from capture_template.planner import PromptLine


def _config() -> PageConfig:
    return PageConfig(
        width_px=1000,
        height_px=1400,
        dpi=100,  # 100 dpi keeps px->pt math easy: 1000px -> 720pt
        margin_px=50,
        prompt_font_px=24,
        prompt_gap_px=10,
        line_height_px=60,
        row_pitch_px=130,
    )


def test_px_to_pt():
    assert px_to_pt(100, 100) == pytest.approx(72.0)
    assert px_to_pt(0, 226) == pytest.approx(0.0)


def test_render_pdf_writes_expected_page_count(tmp_path):
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    lines = [PromptLine(text="a cat", is_drill=False) for _ in range(15)]  # 2 pages
    model = build_layout(lines, targets, _config())
    out = tmp_path / "capture.pdf"
    render_pdf(model, out)
    assert out.exists()
    reader = PdfReader(str(out))
    assert len(reader.pages) == 2
    # page size in points matches px->pt of the configured page size
    media = reader.pages[0].mediabox
    assert float(media.width) == pytest.approx(720.0, abs=1.0)
    assert float(media.height) == pytest.approx(1008.0, abs=1.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_pdf.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'capture_template.pdf'`

- [ ] **Step 3: Write minimal implementation** — `src/capture_template/pdf.py`:
```python
from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen import canvas

from capture_template.layout import LayoutModel


def px_to_pt(px: float, dpi: int) -> float:
    return px / dpi * 72.0


def render_pdf(model: LayoutModel, out_path: str | Path) -> None:
    cfg = model.config
    page_w_pt = px_to_pt(cfg.width_px, cfg.dpi)
    page_h_pt = px_to_pt(cfg.height_px, cfg.dpi)
    c = canvas.Canvas(str(out_path), pagesize=(page_w_pt, page_h_pt))

    for page in model.pages:
        for row in page.rows:
            x0 = px_to_pt(row.bbox.x, cfg.dpi)
            x1 = px_to_pt(row.bbox.x + row.bbox.w, cfg.dpi)

            # writing-line rule at baseline (convert top-left px-y to bottom-left pt-y)
            y_rule = page_h_pt - px_to_pt(row.baseline_y, cfg.dpi)
            c.setStrokeColorRGB(0.8, 0.8, 0.8)
            c.setLineWidth(1)
            c.line(x0, y_rule, x1, y_rule)

            # faint prompt text just above the writing area
            prompt_baseline_px = row.bbox.y - cfg.prompt_gap_px
            y_prompt = page_h_pt - px_to_pt(prompt_baseline_px, cfg.dpi)
            c.setFillColorRGB(0.6, 0.6, 0.6)
            c.setFont("Helvetica", px_to_pt(cfg.prompt_font_px, cfg.dpi))
            c.drawString(x0, y_prompt, row.prompt_text)
        c.showPage()
    c.save()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_pdf.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/pdf.py packages/capture-template/tests/test_pdf.py
git commit -m "feat(capture): add ReportLab PDF renderer"
```

---

### Task 10: Bundled default corpus

**Files:**
- Create: `packages/capture-template/src/capture_template/data/corpus/literature.txt`
- Create: `packages/capture-template/src/capture_template/data/corpus/speeches.txt`
- Modify: `packages/capture-template/src/capture_template/corpus.py`
- Modify: `packages/capture-template/pyproject.toml`
- Test: `packages/capture-template/tests/test_corpus.py` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/test_corpus.py`:
```python
from capture_template.corpus import default_corpus_paths


def test_default_corpus_paths_exist_and_are_nonempty():
    paths = default_corpus_paths()
    assert len(paths) >= 2
    for p in paths:
        assert p.exists()
        assert p.read_text(encoding="utf-8").strip()


def test_default_corpus_loads_usable_sentences():
    charset = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ .,!?'-;:")
    sentences = load_corpus(default_corpus_paths(), charset, min_chars=12, max_chars=120)
    assert len(sentences) >= 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_corpus.py -v`
Expected: FAIL with `ImportError: cannot import name 'default_corpus_paths'`

- [ ] **Step 3: Write the implementation**

Create `src/capture_template/data/corpus/literature.txt` with public-domain prose (Pride and Prejudice / Moby-Dick / A Tale of Two Cities openings — all public domain). Use this exact content:
```
It is a truth universally acknowledged, that a single man in possession of a good fortune, must be in want of a wife.
However little known the feelings or views of such a man may be on his first entering a neighbourhood, this truth is so well fixed in the minds of the surrounding families.
Call me Ishmael.
Some years ago, never mind how long precisely, having little or no money in my purse, I thought I would sail about a little and see the watery part of the world.
It was the best of times, it was the worst of times, it was the age of wisdom, it was the age of foolishness.
It was the epoch of belief, it was the epoch of incredulity, it was the season of light, it was the season of darkness.
Happy families are all alike; every unhappy family is unhappy in its own way.
There was no possibility of taking a walk that day.
The fog comes on little cat feet, sitting over harbor and city on silent haunches.
A screaming comes across the sky; it has happened before, but there is nothing to compare it to now.
```

Create `src/capture_template/data/corpus/speeches.txt` with public-domain speech excerpts. Use this exact content:
```
Four score and seven years ago our fathers brought forth on this continent a new nation, conceived in liberty.
We are met on a great battlefield of that war, and we have come to dedicate a portion of that field.
The world will little note, nor long remember what we say here, but it can never forget what they did here.
We hold these truths to be self evident, that all men are created equal.
Ask not what your country can do for you; ask what you can do for your country.
I have a dream that one day this nation will rise up and live out the true meaning of its creed.
We shall fight on the beaches, we shall fight on the landing grounds, we shall never surrender.
Government of the people, by the people, for the people, shall not perish from the earth.
The only thing we have to fear is fear itself.
Now we are engaged in a great struggle, testing whether that nation can long endure.
```

Add to `src/capture_template/corpus.py` (add `from importlib import resources` to the imports, then the function):
```python
def default_corpus_paths() -> list[Path]:
    base = resources.files("capture_template") / "data" / "corpus"
    return [Path(str(base / "literature.txt")), Path(str(base / "speeches.txt"))]
```

In `pyproject.toml`, ensure the bundled text files ship in the wheel by adding under `[tool.hatch.build.targets.wheel]`:
```toml
[tool.hatch.build.targets.wheel.force-include]
"src/capture_template/data" = "capture_template/data"
```

- [ ] **Step 4: Reinstall (so package data resolves) and run**

Run: `cd packages/capture-template && pip install -e . && python3 -m pytest tests/test_corpus.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/data packages/capture-template/src/capture_template/corpus.py packages/capture-template/pyproject.toml packages/capture-template/tests/test_corpus.py
git commit -m "feat(capture): add bundled default corpus"
```

---

### Task 11: Orchestrator, CLI, and public exports

**Files:**
- Create: `packages/capture-template/src/capture_template/generate.py`
- Modify: `packages/capture-template/src/capture_template/__init__.py`
- Test: `packages/capture-template/tests/test_generate.py`

- [ ] **Step 1: Write the failing test** — `tests/test_generate.py`:
```python
import json

import pytest
from hwfont_schema import CaptureSidecar

import capture_template as ct
from capture_template.generate import generate
from capture_template.layout import PageConfig


def _config() -> PageConfig:
    return PageConfig(
        width_px=1000,
        height_px=1400,
        dpi=226,
        margin_px=50,
        prompt_font_px=24,
        prompt_gap_px=10,
        line_height_px=60,
        row_pitch_px=130,
    )


def test_top_level_exports_present():
    for name in ["generate", "PageConfig", "default_targets", "plan", "build_layout", "__version__"]:
        assert hasattr(ct, name), f"missing export: {name}"


def test_generate_end_to_end(tmp_path):
    # tiny target spec + tiny corpus. `include` is the full lowercase alphabet so the
    # corpus passes the charset filter; letters absent from the corpus get drill-filled.
    # The corpus lines have no terminal punctuation (which is not in the charset), so each
    # line is a single candidate sentence.
    spec = {
        "glyphs": {"count": 1, "include": "abcdefghijklmnopqrstuvwxyz"},
        "ligatures": {"count": 1, "items": ["at"]},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "c.txt").write_text(
        "the cat sat on a mat and the bat ran\n"
        "bees see oats as the trees sway today\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    result = generate(
        target_spec_path=spec_path,
        corpus_dir=corpus_dir,
        out_dir=out_dir,
        config=_config(),
    )

    assert (out_dir / "capture.pdf").exists()
    assert (out_dir / "targets.json").exists()
    sidecar_path = out_dir / "capture.sidecar.json"
    assert sidecar_path.exists()

    # sidecar validates against the contract
    sidecar = CaptureSidecar.model_validate_json(sidecar_path.read_text(encoding="utf-8"))
    region_count = sum(len(p.regions) for p in sidecar.pages)
    assert region_count == len(result.lines)
    assert result.all_met is True


def test_generate_refuses_existing_out_dir_without_force(tmp_path):
    spec = {"glyphs": {"count": 1, "include": "abc "}, "ligatures": {"count": 1, "items": []}}
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "c.txt").write_text("a b c cab cab.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()  # already exists
    with pytest.raises(FileExistsError):
        generate(target_spec_path=spec_path, corpus_dir=corpus_dir, out_dir=out_dir, config=_config())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_generate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'capture_template.generate'`

- [ ] **Step 3: Write minimal implementation**

`src/capture_template/generate.py`:
```python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hwfont_schema import Kind

from capture_template.corpus import default_corpus_paths, load_corpus
from capture_template.layout import LayoutModel, PageConfig, build_layout
from capture_template.pdf import render_pdf
from capture_template.planner import PlanResult, plan
from capture_template.sidecar_out import build_sidecar
from capture_template.targets import default_targets, load_target_spec


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
    )


def generate(
    target_spec_path: str | Path | None,
    corpus_dir: str | Path | None,
    out_dir: str | Path,
    config: PageConfig | None = None,
    force: bool = False,
) -> PlanResult:
    out_dir = Path(out_dir)
    if out_dir.exists() and not force:
        raise FileExistsError(f"output dir already exists: {out_dir} (use force=True to overwrite)")
    out_dir.mkdir(parents=True, exist_ok=True)

    config = config or _default_config()
    targets = (
        load_target_spec(target_spec_path) if target_spec_path is not None else default_targets()
    )

    charset = {t.label for t in targets if t.kind == Kind.single}
    sources = (
        sorted(Path(corpus_dir).glob("*.txt")) if corpus_dir is not None else default_corpus_paths()
    )
    candidates = load_corpus(list(sources), charset)

    result = plan(targets, candidates)
    model: LayoutModel = build_layout(result.lines, targets, config)

    render_pdf(model, out_dir / "capture.pdf")
    (out_dir / "capture.sidecar.json").write_text(
        build_sidecar(model).model_dump_json(), encoding="utf-8"
    )
    (out_dir / "targets.json").write_text(
        json.dumps([t.model_dump(mode="json") for t in targets]), encoding="utf-8"
    )

    unmet = [r.label for r in result.coverage if not r.met]
    print(f"Generated {len(result.lines)} prompt lines across {len(model.pages)} page(s).")
    print(f"Coverage: {sum(r.met for r in result.coverage)}/{len(result.coverage)} targets met.")
    if unmet:
        print(f"UNMET targets ({len(unmet)}): {', '.join(unmet)}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a handwriting-capture PDF + sidecar.")
    parser.add_argument("--target-spec", default=None, help="YAML/JSON target spec (default: built-in)")
    parser.add_argument("--corpus-dir", default=None, help="dir of .txt sources (default: bundled)")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--force", action="store_true", help="overwrite an existing output dir")
    args = parser.parse_args(argv)

    result = generate(
        target_spec_path=args.target_spec,
        corpus_dir=args.corpus_dir,
        out_dir=args.out,
        force=args.force,
    )
    return 0 if result.all_met else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

Replace `src/capture_template/__init__.py`:
```python
from capture_template.generate import generate
from capture_template.layout import LayoutModel, LayoutPage, PageConfig, Row, build_layout, rows_per_page
from capture_template.planner import CoverageRow, PlanResult, PromptLine, count_occurrences, plan
from capture_template.sidecar_out import build_sidecar
from capture_template.targets import default_targets, load_target_spec

__version__ = "0.1.0"

__all__ = [
    "generate",
    "PageConfig",
    "Row",
    "LayoutPage",
    "LayoutModel",
    "build_layout",
    "rows_per_page",
    "plan",
    "count_occurrences",
    "PromptLine",
    "CoverageRow",
    "PlanResult",
    "build_sidecar",
    "default_targets",
    "load_target_spec",
    "__version__",
]
```

- [ ] **Step 4: Run the full test suite**

Run: `cd packages/capture-template && python3 -m pytest -v`
Expected: PASS — every test across every file green.

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/generate.py packages/capture-template/src/capture_template/__init__.py packages/capture-template/tests/test_generate.py
git commit -m "feat(capture): add orchestrator, CLI, and public API"
```

---

## Notes for the Implementer

- **`__init__.py` import order:** `generate` imports from every other module, so importing it first in `__init__.py` is fine — Python resolves the submodules. If you hit a circular-import error, import the leaf modules (`targets`, `planner`, `layout`, `sidecar_out`) before `generate`.
- **Determinism:** the planner's tie-break key `(-score, len(text), text, idx)` is fully ordered, so `plan()` is reproducible; `corpus.load_corpus` preserves first-seen order and `generate` sorts corpus files with `glob` + `sorted`. This is what makes the sidecar reproducible.
- **Coordinate space:** everything in `layout.py`/`sidecar_out.py` is pixel/top-left. Only `pdf.py` converts to ReportLab's point/bottom-left space. Don't leak point math into the other modules.
- **Package data:** the bundled corpus only resolves at runtime after `pip install -e .` re-runs with the `force-include` in place (Task 10) — that's why Task 10 reinstalls before testing.
- **reMarkable Paper Pro default:** `_default_config()` uses 1404×1872 @ 226dpi as a reasonable default page; the capture device's exact raster size can be tuned later without touching any other module.

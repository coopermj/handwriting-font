# capture-template Multi-Page Sample Collection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the capture booklet into a multi-page collection of genuine writing samples — a `--pages` control and a genuine-only variety-fill phase that captures each glyph in many contexts, backed by a larger bundled public-domain corpus.

**Architecture:** Enhances the existing `capture-template` package. `planner.plan` gains a `target_lines` parameter and a novelty-ordered variety-fill phase between coverage and drill-fill. `generate`/CLI gain `--pages` (→ `target_lines = pages * rows_per_page`). `corpus.py` normalizes typography and globs all bundled `*.txt`. The bundled corpus is enlarged. `layout`/`pdf`/`sidecar_out` are unchanged.

**Tech Stack:** Python 3.12, hwfont-schema, ReportLab, PyYAML, pytest. (Runtime stays offline; no network.)

---

## File Structure (changes only)

```
packages/capture-template/
  src/capture_template/
    corpus.py            # + normalize_typography(); default_corpus_paths() globs *.txt
    planner.py           # + _bigrams/_words; plan() gains target_lines + variety-fill phase
    generate.py          # + DEFAULT_PAGES, pages param, --pages CLI, target_lines wiring
    __init__.py          # export DEFAULT_PAGES
    data/corpus/
      literature_extra.txt   # NEW: curated public-domain short sentences (corpus expansion)
  tests/
    test_corpus.py       # + normalization, glob, larger-pool tests
    test_planner.py      # + variety-fill tests
    test_generate.py     # + --pages tests
```

**Conventions:** `python3` for all commands. Append to every commit message (blank line then the line): `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Commit only the files each task names; no caches/artifacts.

---

### Task 1: Typographic normalization in corpus.py

**Files:**
- Modify: `packages/capture-template/src/capture_template/corpus.py`
- Test: `packages/capture-template/tests/test_corpus.py` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/test_corpus.py`:
```python
def test_normalize_typography_maps_to_ascii():
    from capture_template.corpus import normalize_typography

    assert (
        normalize_typography("“Hi—there’s…”")
        == "\"Hi-there's.\""
    )


def test_load_corpus_keeps_sentences_after_normalization(tmp_path):
    # curly quotes + em dash would fail an ASCII charset without normalization
    charset = set("abcdefghijklmnopqrstuvwxyz .,'\"-")
    (tmp_path / "a.txt").write_text(
        "“the cat—a fine cat’s tale” sat here.\n", encoding="utf-8"
    )
    got = load_corpus([tmp_path / "a.txt"], charset, min_chars=8, max_chars=90)
    assert got == ['"the cat-a fine cat\'s tale" sat here.']
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_corpus.py -k normaliz -v`
Expected: FAIL with `ImportError: cannot import name 'normalize_typography'`

- [ ] **Step 3: Write minimal implementation** — in `src/capture_template/corpus.py`, add the normalization map + function after the regex constants (after line `_WHITESPACE = ...`):
```python
_NORMALIZE = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "…": ".",
        " ": " ",
    }
)


def normalize_typography(text: str) -> str:
    """Map common typographic characters (curly quotes, dashes, ellipsis, nbsp) to ASCII."""
    return text.translate(_NORMALIZE)
```
Then, in `load_corpus`, normalize each file's text before splitting. Change:
```python
        text = Path(path).read_text(encoding="utf-8")
```
to:
```python
        text = normalize_typography(Path(path).read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_corpus.py -v`
Expected: PASS (all corpus tests)

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/corpus.py packages/capture-template/tests/test_corpus.py
git commit -m "feat(capture): normalize typography before corpus filtering"
```

---

### Task 2: default_corpus_paths globs all bundled *.txt

**Files:**
- Modify: `packages/capture-template/src/capture_template/corpus.py`
- Test: `packages/capture-template/tests/test_corpus.py` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/test_corpus.py`:
```python
def test_default_corpus_paths_globs_sorted_txt():
    from capture_template.corpus import default_corpus_paths

    paths = default_corpus_paths()
    assert len(paths) >= 2
    assert all(p.suffix == ".txt" for p in paths)
    assert all(p.exists() for p in paths)
    assert paths == sorted(paths)
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_corpus.py -k globs -v`
Expected: This may PASS already against the two hardcoded files, but the implementation below makes it robust to added files. Continue to Step 3 regardless (the glob is required for Task 5).

- [ ] **Step 3: Write minimal implementation** — replace `default_corpus_paths` in `corpus.py` with:
```python
def default_corpus_paths() -> list[Path]:
    base = resources.files("capture_template") / "data" / "corpus"
    return sorted(Path(str(base)).glob("*.txt"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_corpus.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/corpus.py packages/capture-template/tests/test_corpus.py
git commit -m "feat(capture): default_corpus_paths globs all bundled txt files"
```

---

### Task 3: Variety-fill phase in planner

**Files:**
- Modify: `packages/capture-template/src/capture_template/planner.py`
- Test: `packages/capture-template/tests/test_planner.py` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/test_planner.py`:
```python
def test_plan_without_target_lines_is_unchanged():
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    result = plan(targets, ["cat", "bat", "rat"])  # coverage met by one line; no fill
    assert len(result.lines) == 1
    assert all(not line.is_drill for line in result.lines)


def test_plan_variety_fill_reaches_target_lines_with_genuine_only():
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    candidates = ["cat", "bat", "rat", "mat", "hat", "sat", "fat", "pat"]
    result = plan(targets, candidates, target_lines=5)
    assert len(result.lines) == 5
    assert all(not line.is_drill for line in result.lines)


def test_plan_variety_fill_stops_at_pool_exhaustion_without_padding():
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    result = plan(targets, ["cat", "bat"], target_lines=10)
    assert len(result.lines) == 2  # only two genuine available; not padded to 10
    assert all(not line.is_drill for line in result.lines)


def test_plan_variety_fill_prefers_more_novel_sentences():
    targets = [Target(label="x", kind=Kind.single, required_count=1)]
    # coverage met by "xx"; fill then prefers the sentence adding the most new bigrams
    result = plan(targets, ["xx", "abcd", "ab"], target_lines=2)
    assert result.lines[0].text == "xx"
    assert result.lines[1].text == "abcd"  # 3 new bigrams (ab,bc,cd) beats "ab" (1)


def test_plan_coverage_wins_over_small_target_lines():
    # base coverage needs more lines than target_lines=1 can hold; coverage still met
    targets = [
        Target(label="a", kind=Kind.single, required_count=1),
        Target(label="z", kind=Kind.single, required_count=1),
    ]
    result = plan(targets, ["aaa", "zzz"], target_lines=1)
    assert result.all_met is True
    assert len(result.lines) >= 2  # one line per letter, target_lines floor doesn't cap coverage
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_planner.py -v`
Expected: FAIL — `plan() got an unexpected keyword argument 'target_lines'`

- [ ] **Step 3: Write minimal implementation** — in `planner.py`, add the two helpers after `_drill_lines`:
```python
def _bigrams(text: str) -> set[tuple[str, str]]:
    """Adjacent non-space character pairs — a proxy for glyph-in-context coverage."""
    return {(a, b) for a, b in zip(text, text[1:]) if a != " " and b != " "}


def _words(text: str) -> set[str]:
    return set(text.split())
```
Then replace the entire `plan` function (currently lines ~69–117) with:
```python
def plan(
    targets: list[Target],
    candidates: list[str],
    line_cap: int = 200,
    drill_budget: int = 60,
    target_lines: int | None = None,
) -> PlanResult:
    deficit = {t.label: t.required_count for t in targets}
    achieved_natural = {t.label: 0 for t in targets}
    achieved_drill = {t.label: 0 for t in targets}

    effective_cap = max(line_cap, target_lines) if target_lines is not None else line_cap

    pool = list(enumerate(candidates))
    lines: list[PromptLine] = []

    # Phase 1 — coverage: meet base counts with genuine sentences.
    while len(lines) < effective_cap and any(d > 0 for d in deficit.values()):
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

    # Phase 2 — variety-fill: add genuine sentences (never drills) up to target_lines,
    # choosing the most context-novel candidate each step (new bigrams, then new words).
    if target_lines is not None:
        seen_bigrams: set[tuple[str, str]] = set()
        seen_words: set[str] = set()
        for line in lines:
            seen_bigrams |= _bigrams(line.text)
            seen_words |= _words(line.text)
        while len(lines) < target_lines and pool:
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
            seen_bigrams |= _bigrams(text)
            seen_words |= _words(text)
            for t in targets:
                occ = count_occurrences(text, t.label)
                achieved_natural[t.label] += occ
                deficit[t.label] = max(0, deficit[t.label] - occ)

    # Phase 3 — drill-fill remaining base-coverage gaps, respecting effective_cap.
    for t in targets:
        if len(lines) >= effective_cap:
            break
        if deficit[t.label] <= 0:
            continue
        for drill_text in _drill_lines(t.label, deficit[t.label], drill_budget):
            if len(lines) >= effective_cap:
                break
            lines.append(PromptLine(text=drill_text, is_drill=True))
            occ = count_occurrences(drill_text, t.label)
            achieved_drill[t.label] += occ
            deficit[t.label] = max(0, deficit[t.label] - occ)

    coverage = _coverage(targets, achieved_natural, achieved_drill)
    return PlanResult(lines=lines, coverage=coverage, all_met=all(r.met for r in coverage))
```
(Leave `_coverage`, `_score`, `_drill_lines`, `count_occurrences`, and the dataclasses unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_planner.py -v`
Expected: PASS (existing planner tests + the 5 new ones)

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/planner.py packages/capture-template/tests/test_planner.py
git commit -m "feat(capture): add genuine-only variety-fill phase to planner"
```

---

### Task 4: --pages control in generate + CLI

**Files:**
- Modify: `packages/capture-template/src/capture_template/generate.py`
- Modify: `packages/capture-template/src/capture_template/__init__.py`
- Test: `packages/capture-template/tests/test_generate.py` (append)

- [ ] **Step 1: Write the failing test** — append to `tests/test_generate.py` (the file already imports `generate`, `main`, `PageConfig`, `json`, `pytest`):
```python
def _big_corpus_dir(tmp_path):
    nato = (
        "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
        "mike november oscar papa quebec romeo sierra tango uniform victor whiskey "
        "xray yankee zulu"
    ).split()
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    sents = [f"the quick brown fox jumps over {w} and lazy dogs run." for w in nato]
    (corpus_dir / "c.txt").write_text("\n".join(sents), encoding="utf-8")
    return corpus_dir


def _alpha_spec(tmp_path):
    # include a period so sentences split, and so '.' is a satisfiable single-glyph target
    spec = {
        "glyphs": {"count": 1, "include": "abcdefghijklmnopqrstuvwxyz."},
        "ligatures": {"count": 1, "items": []},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return spec_path


def test_generate_pages_controls_booklet_size(tmp_path):
    cfg = PageConfig(
        width_px=1000, height_px=1400, dpi=226, margin_px=50,
        prompt_font_px=24, prompt_gap_px=10, line_height_px=60, row_pitch_px=130,
    )  # rows_per_page == 10
    result = generate(
        target_spec_path=_alpha_spec(tmp_path),
        corpus_dir=_big_corpus_dir(tmp_path),
        out_dir=tmp_path / "out",
        config=cfg,
        pages=2,
    )
    assert len(result.lines) == 20  # 2 pages * 10 rows, filled with genuine prose
    assert all(not line.is_drill for line in result.lines)


def test_generate_rejects_pages_below_one(tmp_path):
    with pytest.raises(ValueError):
        generate(
            target_spec_path=None, corpus_dir=None, out_dir=tmp_path / "out", pages=0
        )
    assert not (tmp_path / "out").exists()  # invalid arg rejected before any dir is created


def test_main_accepts_pages_flag(tmp_path):
    rc = main([
        "--target-spec", str(_alpha_spec(tmp_path)),
        "--corpus-dir", str(_big_corpus_dir(tmp_path)),
        "--out", str(tmp_path / "out"),
        "--pages", "2",
    ])
    assert rc == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_generate.py -k "pages" -v`
Expected: FAIL — `generate() got an unexpected keyword argument 'pages'`

- [ ] **Step 3: Write minimal implementation**

In `generate.py`, update the layout import (add `rows_per_page`):
```python
from capture_template.layout import LayoutModel, PageConfig, build_layout, rows_per_page
```
Add a constant after the imports / `UnmetCoverageError`:
```python
DEFAULT_PAGES = 12
```
Change the `generate` signature to add `pages`:
```python
def generate(
    target_spec_path: str | Path | None,
    corpus_dir: str | Path | None,
    out_dir: str | Path,
    config: PageConfig | None = None,
    force: bool = False,
    allow_unmet: bool = False,
    pages: int = DEFAULT_PAGES,
) -> PlanResult:
```
At the very top of `generate` (before the `out_dir` existence check), validate `pages`:
```python
    if pages < 1:
        raise ValueError(f"pages must be >= 1, got {pages}")
```
Inside the `try`, after `config = config or _default_config()` and after `candidates = load_corpus(...)`, compute `target_lines` and pass it to `plan`:
```python
        target_lines = pages * rows_per_page(config)
        result = plan(targets, candidates, target_lines=target_lines)
```
(Replace the existing `result = plan(targets, candidates)` line.)
Update the summary print to report genuine vs. drill:
```python
        genuine = sum(1 for line in result.lines if not line.is_drill)
        drills = len(result.lines) - genuine
        print(
            f"Generated {len(result.lines)} prompt lines "
            f"({genuine} genuine, {drills} drill) across {len(model.pages)} page(s)."
        )
```
(Replace the existing `print(f"Generated {len(result.lines)} prompt lines across ...")` line; keep the coverage and UNMET prints below it.)

In `main`, add a positive-int type and the `--pages` flag, and pass it through. Add this helper above `main`:
```python
def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed
```
Add the argument (next to the others):
```python
    parser.add_argument(
        "--pages", type=_positive_int, default=DEFAULT_PAGES,
        help=f"target booklet length in pages (default: {DEFAULT_PAGES})",
    )
```
And pass it in the `generate(...)` call inside `main`:
```python
            pages=args.pages,
```

In `__init__.py`, export `DEFAULT_PAGES`: add it to the `from capture_template.generate import ...` line and to `__all__`:
```python
from capture_template.generate import DEFAULT_PAGES, UnmetCoverageError, generate
```
(and add `"DEFAULT_PAGES"` to the `__all__` list.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python3 -m pytest tests/test_generate.py -v`
Expected: PASS (existing generate tests + the 3 new ones). Then run the full suite: `python3 -m pytest -v` — all green.

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/generate.py packages/capture-template/src/capture_template/__init__.py packages/capture-template/tests/test_generate.py
git commit -m "feat(capture): add --pages booklet-size control"
```

---

### Task 5: Enlarge the bundled corpus

**Files:**
- Create: `packages/capture-template/src/capture_template/data/corpus/literature_extra.txt`
- Test: `packages/capture-template/tests/test_generate.py` (append)

This task expands the bundled public-domain corpus so a default run produces a multi-page, predominantly-genuine booklet. Per the spec, the deterministic baseline is a curated set of short public-domain sentences (proverbs, fables' morals, Shakespeare, traditional rhymes — all public domain). Maintainers can later drop additional public-domain `*.txt` files (e.g. Project Gutenberg works with boilerplate stripped) into `data/corpus/`; `default_corpus_paths()` (Task 2) already globs them.

- [ ] **Step 1: Write the failing test** — append to `tests/test_generate.py`:
```python
def test_default_corpus_yields_multipage_mostly_genuine_booklet(tmp_path):
    from hwfont_schema import CaptureSidecar, Kind

    from capture_template.corpus import default_corpus_paths, load_corpus
    from capture_template.targets import default_targets

    targets = default_targets()
    charset = {t.label for t in targets if t.kind == Kind.single}
    pool = load_corpus(default_corpus_paths(), charset, max_chars=90)
    assert len(pool) >= 40  # healthy genuine pool

    result = generate(target_spec_path=None, corpus_dir=None, out_dir=tmp_path / "out")
    genuine = sum(1 for line in result.lines if not line.is_drill)
    assert genuine >= 35  # the booklet is mostly real prose, not drills

    sidecar = CaptureSidecar.model_validate_json(
        (tmp_path / "out" / "capture.sidecar.json").read_text(encoding="utf-8")
    )
    assert len(sidecar.pages) >= 3  # genuinely multi-page
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python3 -m pytest tests/test_generate.py -k multipage -v`
Expected: FAIL — the current bundled pool is ~18 sentences, so `len(pool) >= 40` fails.

- [ ] **Step 3: Write the implementation** — create `src/capture_template/data/corpus/literature_extra.txt` with EXACTLY these public-domain lines:
```
Slow and steady wins the race.
Look before you leap.
Necessity is the mother of invention.
United we stand, divided we fall.
One good turn deserves another.
Do not count your chickens before they are hatched.
Familiarity breeds contempt.
It is easy to be brave from a safe distance.
A penny saved is a penny earned.
Early to bed and early to rise makes a man healthy and wise.
Honesty is the best policy.
Where there is a will, there is a way.
Actions speak louder than words.
The early bird catches the worm.
Birds of a feather flock together.
When in Rome, do as the Romans do.
Practice makes perfect.
Many hands make light work.
Out of sight, out of mind.
Rome was not built in a day.
The pen is mightier than the sword.
Time and tide wait for no man.
Two wrongs do not make a right.
A friend in need is a friend indeed.
Curiosity killed the cat.
Every cloud has a silver lining.
Fortune favours the bold.
All for one, and one for all.
We are such stuff as dreams are made on.
All the world's a stage, and all the men and women merely players.
Brevity is the soul of wit.
To be, or not to be, that is the question.
Now is the winter of our discontent.
The course of true love never did run smooth.
Twinkle, twinkle, little star, how I wonder what you are.
Jack and Jill went up the hill to fetch a pail of water.
Humpty Dumpty sat on a wall; Humpty Dumpty had a great fall.
Cowards die many times before their deaths.
```

- [ ] **Step 4: Reinstall (so the new data file ships) and run**

Run: `cd packages/capture-template && pip install -e . && python3 -m pytest tests/test_generate.py -k multipage -v`
Expected: PASS. (`pip install -e .` re-runs the `force-include` so the new file is part of the package; add `--break-system-packages` only if the environment requires it.) Then run the full suite `python3 -m pytest -v` — all green.

- [ ] **Step 5: Commit**
```bash
git add packages/capture-template/src/capture_template/data/corpus/literature_extra.txt packages/capture-template/tests/test_generate.py
git commit -m "feat(capture): enlarge bundled public-domain corpus for multi-page booklets"
```

---

## Notes for the Implementer

- **Determinism is preserved.** The variety-fill tie-break key `(-new_bigrams, -new_words, len, text, idx)` is fully ordered, so the same inputs produce the same booklet and the same sidecar bytes.
- **`target_lines=None` is the old behavior.** When `--pages`/`pages` isn't in play (library callers passing nothing get `DEFAULT_PAGES`; pass an explicit small value for short booklets), `effective_cap == line_cap` and phase 2 is skipped — existing planner tests stay valid.
- **Coverage is always the floor.** Phase 1 runs to meet base counts regardless of `target_lines`; phase 2 only *adds* genuine prose; phase 3 drills only true coverage gaps and never pads to hit the page target.
- **Corpus scaling.** `data/corpus/` is globbed, so dropping more public-domain `*.txt` files there (whole Gutenberg works with boilerplate stripped, or a user's own texts) scales the genuine pool into the hundreds and fills more pages — no code change needed. The curated `literature_extra.txt` is the guaranteed-offline baseline.
- **All public-domain.** Every line in `literature_extra.txt` is a proverb, fable moral, traditional rhyme, or pre-1929 work (Shakespeare) — no copyrighted text.

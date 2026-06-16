# capture-template — Multi-Page Sample Collection (Enhancement Design)

**Date:** 2026-06-15
**Status:** Approved for planning
**Module:** `capture-template` (enhancement to the already-built module)
**Depends on:** `hwfont-schema` (unchanged)
**Parent specs:** [capture-template design](2026-06-15-capture-template-design.md), [architecture](2026-06-15-handwriting-font-architecture-design.md)

## Purpose

Expand the capture booklet from a small, drill-dominated collection into a substantial **multi-page collection of genuine writing samples**. Today a default run produces ~18 genuine sentences plus dozens of mechanical drill lines (because only ~18 short sentences fit the page). This enhancement enlarges the bundled public-domain corpus and adds a *variety-fill* phase so the booklet captures each glyph in **many more contexts** — which directly improves the downstream font's contextual alternates and ligatures — while keeping drills reserved only for coverage gaps the corpus can't fill.

## Design Decisions (settled during brainstorming)

1. **Goal = capture beyond bare coverage for variety** (not merely fewer drills, and not higher per-glyph counts). The base target counts (12× glyph / 8× ligature) remain the *coverage floor*; genuine prose is added *above* the floor.
2. **Size control = target page count** (`--pages N`). `generate` converts pages → target lines via `rows_per_page(config)`. Default `DEFAULT_PAGES = 12` so the default booklet is a fuller multi-page collection.
3. **Extra material is genuine-only, novelty-ordered.** Drills are never added to reach the page target; the fill picks sentences that maximize new glyph-context (character bigrams, then new words).
4. **Corpus is enlarged with real public-domain works** (literature + speeches) bundled as plain-text data, so the usable sentence pool is in the hundreds.

## Scope

All changes are within `packages/capture-template/`. The base target counts and the Contract X output structure are unchanged — the booklet simply contains more lines across more pages.

| File | Change |
|---|---|
| `data/corpus/*.txt` | Enlarge: bundle several real public-domain works (excerpts), literature + speeches. |
| `corpus.py` | `default_corpus_paths()` globs all bundled `*.txt`; add typographic normalization before splitting. |
| `planner.py` | Add a variety-fill phase (`target_lines` param) between coverage and drill-fill. |
| `generate.py` + CLI | Add `--pages N` / `pages` param; compute `target_lines`; print booklet size. |
| `layout.py`, `pdf.py`, `sidecar_out.py` | **Unchanged** — already paginate/render/emit any number of lines. |

## The Variety-Fill Algorithm

`plan()` gains one optional parameter — `target_lines: int | None = None` — and runs three phases in order:

1. **Coverage (existing, unchanged).** Greedily select genuine sentences until every base deficit (12×/8×) is met or no genuine sentence reduces it. Always runs first; this is the floor.
2. **Variety-fill (new).** If `target_lines` is set and the current line count is below it, repeatedly pick from the *remaining* genuine candidates the one with the highest **novelty score**, recomputing after each pick:
   - Primary key: count of **new character bigrams** (adjacent non-space glyph pairs) the sentence contributes that aren't yet in the running `seen_bigrams` set.
   - Secondary key: count of **new whole words** not yet seen.
   - Tie-break: stable `(-bigram_novelty, -word_novelty, len(text), text, original_index)`.
   Continue until the line count reaches `target_lines` or the genuine pool is exhausted. All added lines are `is_drill=False` and also reduce any remaining base deficits.
3. **Drill-fill (existing).** Only for base-coverage targets still short after phases 1–2 (the genuinely rare letters). Drills are **never** added to reach `target_lines`.

**Properties:**
- **Coverage always wins** — a small `--pages` cannot drop the booklet below what coverage requires; `target_lines` is a floor for genuine content, not a cap below coverage.
- **Corpus-limited and logged** — if the genuine pool is exhausted before `target_lines`, the booklet is shorter and that is reported; never padded with redundant filler or drills.
- **Front-loads variety** — the most context-diverse sentences are selected first, so even a truncated booklet maximizes glyph-in-context coverage.
- **Deterministic** — no randomness; fully-ordered tie-breaks keep the emitted sidecar reproducible.
- **`line_cap`** becomes a safety ceiling raised to at least `target_lines` when set; with `target_lines=None`, behavior is exactly as today (coverage + drills).

## Page Control (`generate` / CLI)

- `generate(..., pages: int | None = None)`. When `pages` is given (or defaulted), `target_lines = pages * rows_per_page(config)`, passed to `plan`.
- CLI: `--pages N` (int). Default `DEFAULT_PAGES = 12` when omitted, so the default booklet is a fuller collection. `--pages` overrides up or down.
- `generate` prints the resulting booklet size (genuine vs. drill line counts, page count) and any unmet/under-target note.

## Corpus Expansion

- Bundle several real public-domain works as plain-text files under `src/capture_template/data/corpus/`: a mix of **literature** (e.g. Austen, Carroll, Dickens, Doyle, Aesop's short fables) and **speeches/orations** (a public-domain orations collection / Lincoln addresses). Sourced from Project Gutenberg during implementation via WebFetch.
- **Strip Project Gutenberg boilerplate** — the `*** START ... ***` / `*** END ... ***` markers, header, and license text — keeping only the public-domain work body. (The works are public domain; we do not reproduce Gutenberg's license/trademark.)
- Bundle **excerpts** (~80–120 KB per work) rather than whole books to keep the package lean; the existing splitter + ≤90-char filter still yields hundreds of usable sentences — enough for a 12-page genuine booklet.
- `corpus.py`:
  - `default_corpus_paths()` returns **all `*.txt` under `data/corpus/`**, sorted (so adding files needs no code change).
  - A small, deterministic **typographic normalization** pass before splitting: curly quotes → straight, em/en-dash → hyphen, ellipsis → period. Lets genuine Gutenberg prose pass the charset filter instead of being needlessly rejected.
- **Runtime independence:** the bundled files make the tool work offline. WebFetch is used only at implementation time to source the texts. If WebFetch is unavailable then, fall back to a hand-curated short-sentence set; the module never fetches at runtime.

## Error Handling

Consistent with the module's "flag, never silently mislead" rule:
- `--pages` must be ≥ 1 (validated; `< 1` raises a clear error).
- Genuine pool can't reach `target_lines` → shorter booklet, logged (genuine lines vs. target). No drill padding.
- Existing guarantees unchanged: base coverage met or `UnmetCoverageError` raised; drills flagged; filtered-sentence counts logged.

## Testing Strategy (TDD)

- **corpus:** `default_corpus_paths()` globs all bundled `*.txt` (≥ several files); normalization maps curly quotes / em-dash / ellipsis to ASCII; the default pool yields a healthy count of usable sentences at `max_chars=90` (assert a solid lower bound, e.g. ≥ 150).
- **planner:** with `target_lines` set and a sufficient fixture pool, genuine lines reach `target_lines`; all fill lines are non-drill; novelty ordering prefers higher-new-bigram sentences (crafted fixture); coverage still met; `target_lines` below coverage need doesn't shrink below coverage; pool exhaustion stops the fill short without padding; identical inputs → identical lines.
- **generate:** `--pages N` yields ≈ N pages (≥ coverage); default `pages=12` produces a multi-page booklet; `pages < 1` raises; end-to-end booklet validates against `hwfont_schema`, region count == line count, and the genuine-line fraction is high.
- **layout / pdf / sidecar:** unchanged; existing tests stay green.

## Technology

- **Implementation-time only:** WebFetch (to source public-domain texts from Project Gutenberg).
- **Runtime:** unchanged — Python 3.12, ReportLab, PyYAML, `hwfont-schema`; no network.
- **New code:** typographic normalization map in `corpus.py`; variety-fill phase in `planner.py`; `--pages` wiring in `generate.py`.

## Out of Scope

- Changing the base target counts or the Contract X schema.
- Runtime corpus fetching.
- Prompt-text line wrapping (sentences still bounded by `max_chars` to fit one writing line).
- Non-Latin scripts.

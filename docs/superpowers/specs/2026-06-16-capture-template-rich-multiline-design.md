# capture-template — Multi-Line Copy & Cluster-Rich Quotations (Enhancement Design)

**Date:** 2026-06-16
**Status:** Approved for planning
**Module:** `capture-template` (enhancement to the already-built module)
**Depends on:** `hwfont-schema` (unchanged)
**Parent specs:** [capture-template design](2026-06-15-capture-template-design.md), [multi-page](2026-06-15-capture-template-multipage-design.md), [architecture](2026-06-15-handwriting-font-architecture-design.md)

## Purpose

Two related enrichments to the capture booklet:

1. **Longer copy across multiple lines** — let a quotation exceed one writing line and wrap across several consecutive ruled lines, so the booklet can capture handwriting in longer, more natural passages (not just ≤90-char fragments).
2. **Cluster-rich quotations + targets** — add common ligatures, the `oft` cluster (in both `oft-` and `-oft` positions), and common digraphs/trigraphs — **including their capital-initial forms** (sentence starts, proper nouns) — as coverage targets, backed by curated public-domain quotations so each is captured in genuine context. This directly serves the font's ligature and contextual-alternate features.

## Design Decisions (settled during brainstorming)

1. **Multi-line layout = interleaved wrapped rows.** A long quotation stays a single *entry* through selection, then renders as several consecutive rows — each the existing row design (faint prompt above a blank writing line) with its own sidecar region and a known transcript fragment. A long quote never fragments during selection; segmentation still gets one transcript per writing line.
2. **New clusters are coverage targets *and* get curated quotations.** Targets without quotations would drill (`tch tch tch`); quotations without targets give no guarantee. Both together capture each cluster in real prose.
3. **Capital-initial cluster variants are distinct targets.** `Th` ≠ `th` (case-sensitive matching), captured via sentence openers and proper nouns.
4. **Tiered counts to avoid manufacturing drills.** Common clusters keep the standard count; rare ones get lower counts; curated quotations supply them; drills remain a last-resort fallback only.

## Part 1 — Multi-Line Long Copy

A long quotation is selected as one entry, then wrapped into consecutive rows at render time. A single wrap function drives both the planner's row-cost accounting and the layout's rendering, so the PDF and the `--pages` math agree.

### Components

- **`corpus.py`** — raise the length cap so longer quotations survive: `max_chars` ~240 (bounding a quote to ~3 wrapped lines); `min_chars` unchanged (12). Normalization and glob unchanged.
- **New `text_wrap.py`** — pure `wrap_text(text: str, max_line_chars: int) -> list[str]`: greedy word-wrap to a character budget; a lone word longer than the budget gets its own line; empty string → `[]`. Deterministic, no font-metric dependency (keeps `layout.py` ReportLab-free).
- **`layout.py`** — `PageConfig` gains `max_line_chars: int` (validated ≥ 1 in `_validate`). `build_layout` wraps each entry into segments and emits **one Row per segment** (per-segment `prompt_text`, `expected_transcript`, `expected_units`, `bbox`, `baseline_y`). A long quote becomes consecutive rows; a wrapped quote may straddle a page break (accepted — each row is an independent capture region). Fixes the existing prompt-overflow nit.
- **`planner.py`** — selection logic (coverage greedy, novelty variety-fill, drill-fill) unchanged, but its caps count **rendered rows**, not entries: each candidate's row-cost = `len(wrap_text(text, max_line_chars))`, drills cost 1, and `effective_cap` / `target_lines` / `fill_limit` are measured in rendered rows. Coverage and novelty scoring still run on the full entry text. A coverage entry may overshoot a cap by its own wrap-count (coverage wins); variety-fill stops before exceeding `fill_limit`. `max_line_chars=None` preserves today's one-row-per-entry behavior.
- **`generate.py`** — computes `max_line_chars` from the page config and passes it to both `plan` and `build_layout`, so `--pages` (rows) stays accurate when one entry spans several rows.
- **`sidecar_out.py`** — unchanged; it already emits one region per Row, so wrapped rows become regions automatically.

## Part 2 — Cluster Targets & Quotations

Extend the default target spec with a curated, common cluster set (all ligature-kind), and add curated public-domain quotation files that supply them in genuine context.

### Target additions (ligature-kind)

On top of the existing `fi fl ff ffi ffl eft fore ough tion ing`:

- **Common ligatures:** `st`, `ct`, `sp`, `ll`, `ss`, `ee`, `oo`
- **`oft`** — one target; quotations cover it word-initially (*often, oft*) and word-finally (*soft, loft, aloft*).
- **Lowercase digraphs:** `th`, `ch`, `sh`, `wh`, `ph`, `ck`, `ng`, `qu`, `gh`, `ea`, `ou`
- **Lowercase trigraphs:** `tch`, `dge`, `igh`, `ght`, `thr`, `str`, `nth`
- **Capital digraphs:** `Th`, `Ch`, `Sh`, `Wh`, `Ph`, `St`, `Sp`, `Qu`
- **Capital trigraphs:** `Thr`, `Str`, `Sch`, `Shr`

### Tiered required counts

- **Common (count 8):** clusters met trivially by general prose — `st ll ss ee oo th ch sh wh ck ng qu gh ea ou`.
- **Common capital digraphs (count 6):** met by sentence openers — `Th Sh Wh St`.
- **Rare lowercase (count 4):** need targeted quotations — `ct sp ph oft tch dge igh ght thr str nth`.
- **Rare capital (count 3):** need sentence-initial words / proper nouns — `Ch Ph Sp Qu Thr Str Sch Shr`.

Matching is **case-sensitive** (`count_occurrences` uses `str.find`), so `Th` and `th` are independent targets with independent counts.

### Quotation additions

New bundled public-domain files under `data/corpus/` (globbed automatically):
- `clusters.txt` — sentences dense in the rare lowercase clusters (e.g. *catch, watch, fetch; bridge, judge, edge; night, fright, delight; often, soft, aloft; through, three; strong, street; tenth, month*).
- `proper_nouns.txt` — sentences led by capitalized clusters and proper nouns (*Three…, Strong…, School…, Shrewd…, Philip…, Quentin…, Sparta…, Strasbourg…, Thrace…*).
- Existing `literature.txt` / `speeches.txt` / `literature_extra.txt` already cover the common clusters and capital `Th/Sh/Wh/St` incidentally.

All new lines are genuine public-domain text (pre-1929 works, proverbs, traditional rhymes) or factual proper-noun sentences; no copyrighted material.

## Error Handling

- `wrap_text`: lone over-long word → own line; empty → `[]`; terminates always.
- `max_line_chars < 1` → `ValueError` in `layout._validate`.
- Every new cluster is short enough to drill as a last resort, so coverage stays satisfiable (no `UnmetCoverageError` for clusters); curated quotations keep them out of drill form.
- Planner caps measured in rendered rows; coverage may overshoot by one entry's wrap-count; variety-fill stops before exceeding `fill_limit`; loop terminates (pool only shrinks).
- Pagination may straddle a multi-row quote across a page break — accepted.

## Testing Strategy (TDD)

- **text_wrap:** word-boundary wrapping ≤ budget; lone over-long word on its own line; short text → one segment; empty → `[]`; deterministic.
- **corpus:** quotations up to the new `max_chars` survive; still-too-long ones dropped.
- **planner:** a 3-row quote counts as 3 toward `effective_cap`/`target_lines`/`fill_limit`; coverage still met; deterministic; `max_line_chars=None` → unchanged single-row behavior.
- **layout:** a long entry wraps into N consecutive Rows with correct per-segment transcript/`expected_units`/bbox/baseline; rows paginate across pages; `max_line_chars < 1` raises.
- **targets:** `default_targets()` includes the new lowercase + capital cluster targets at their tiered counts; `Th` vs `th` are distinct.
- **generate / end-to-end:** default run `all_met`; a long quotation renders as multiple regions; sidecar regions == rendered rows; new clusters covered predominantly by genuine prose; new quotation files validate as usable corpus.

## Technology

- No new runtime dependencies. `text_wrap` is pure Python.
- `max_line_chars` derived from `PageConfig` (usable width + prompt font), with a conservative budget so wrapped lines fit the column.

## Out of Scope

- Font-side or segmentation handling of position/case (the capture side only ensures both contexts appear in the prompts).
- Orphan/widow control across page breaks for multi-row quotations.
- Exhaustive digraph/trigraph enumeration — a curated common set only.
- Non-Latin scripts.

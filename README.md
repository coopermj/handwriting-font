# handwriting-font

Make a sophisticated, modern, feature-rich font from your handwriting using AI and a set of python scripts.

This project has the following two components:

## Handwriting Capture Templates

Python script creates a LaTeX template to create a PDF file for capturing handwriting. This may be printed, but initial use will be 
making a PDF on which to write using a RemarkablePaper Pro. Instead of being letter-by-letter capture, this will create sentences to 
capture glyphs in context of other glyphs and generate a rich library of ligatures. These ligatures will be beyond the normal standard (fi, ffi) to include syllable- and word-combinations like "fore" and "eft."

Each glyph must be captured at least a dozen times and word combinations many times. This template must be easily consumed by the second step.

## Handwriting Font Creation

Ingesting the handwriting from the template above, the font generator will create a font that mimics the style of penmanship of the person whose handwriting was captured. Using contextual alternates and complicated ligature substitution to select the appropriate glyph or ligature for a given situation and cycle through common glyphs (e.g.: a, I, e) to keep variation high. 
## Development setup

Python 3.12+ is required; Homebrew's `python3.14` is known to work (3.15 lacks a `scikit-image` wheel). Create the repo-level virtualenv and install every package in editable mode with its dev extras:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -e "packages/hwfont-schema[dev]" -e "packages/capture-template[dev]" -e "packages/ingest-segment[dev]" -e "packages/font-gen[dev,raster]" -e "packages/review[dev,ui]"
```

Run a package's tests from its directory, e.g. `cd packages/capture-template && ../../.venv/bin/python -m pytest -q`.

Build the synthetic demo font with `font-gen demo --out some/dir --ttf` and open the `.proof.svg` it writes beside the font. How each host (browsers, Word) handles variant cycling across spaces is in [docs/font-gen-hosts.md](docs/font-gen-hosts.md).

Review segmented candidates into a glyph store with the `review` CLI. Seed the store's targets with `review init --store S --sidecar capture.sidecar.json`, then run `review serve --store S --sidecar capture.sidecar.json --candidates DIR [--raster p0=page.png]` to open the canvas review UI at http://127.0.0.1:8765, or apply decisions headlessly with `review apply ... --accept ID`. Every decision is written through to the store and recorded in `review.json` beside `candidates.json`, and `review replay` rebuilds a store's samples from those files. Check progress against the targets with `review coverage`, then build the font with `font-gen build --store S --out Hand.otf`.

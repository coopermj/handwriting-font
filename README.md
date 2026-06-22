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
# font-gen: host notes

## Word

Word: Font → Advanced → tick "Use Contextual Alternates" and set Ligatures to "All"; otherwise the base glyphs render

## Manual checklist: does variant cycling cross spaces?

Whether a host shapes `calt` across spaces depends on its word-shaping cache, not only on GSUB (spec, Known Risk 4). Build the font, then render each test string in each host below, once with default styling and once with explicit feature settings (in CSS: `font-feature-settings: "calt"` and `font-kerning: normal`).

Test strings: `the the the`, `aaaa`, `nation`, `ffi`, `office`

Pass criteria: `the the the` shows three different renderings of the words when the counter crosses spaces; `aaaa` cycles through variants; `nation` and `ffi` / `office` show ligatures where the store has them.

| Host (engines) | Default styling | With `font-feature-settings: "calt"` / `font-kerning: normal` | Counter crosses spaces? |
| --- | --- | --- | --- |
| Blink (Chrome, Edge, Electron) | | | [ ] yes  [ ] no |
| Gecko (Firefox) | | | [ ] yes  [ ] no |
| CoreText (TextEdit, Pages, Safari) | | | [ ] yes  [ ] no |
| DirectWrite (Word, WPF) | | | [ ] yes  [ ] no |

Notes per host:

- Blink: its `CachingWordShaper` shapes space-delimited words independently on default-styled text; whether explicit CSS feature settings switch it to whole-run shaping for this font is not established offline.
- Gecko: expected to work (the font's GSUB references the space glyph via `@TRANSPARENT`, which disables the word cache); unverified.
- CoreText: shapes whole runs; confirm.
- DirectWrite: whether `calt` chains see across a space is not established offline; in Word, contextual alternates are assumed off by default (see above).

## Fallback

If a host breaks runs at spaces, rebuild with `--cycle-mode word`. The counter then restarts at every word, so repeated words render identically in that host only. The base-glyph rendering stands on its own.

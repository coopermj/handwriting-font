"""Proof sheet (spec §12.1): rows of text shaped with HarfBuzz and drawn from the *built binary* as a canonical SVG.

Each glyph row is one `<g>` holding one `<path>` per shaped glyph, positioned in font units inside the group, so a
row's `<path>` lines depend only on its text: the `proof` verb's rows compare line-wise with the build's.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from io import BytesIO
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

from font_gen.naming import POS_ORDER, base_name, label_key
from font_gen.qa import OFF3, shape_glyphs

if TYPE_CHECKING:
    from font_gen.load import LoadedSample
    from font_gen.select import GlyphPlan

__all__ = [
    "FONT_PX", "LABEL_X", "LEFT", "OFF3", "PANGRAMS", "ROW_H", "Row",
    "cairosvg_available", "default_rows", "proof_rows_from_font", "render_png", "render_proof",
]

Row = tuple[str, str] | tuple[str, str, dict[str, bool]]  # label, text, optional feature overrides

FONT_PX = 64
ROW_H = 110
LEFT = 40
LABEL_X = 8
LABEL_DY = 20
BASELINE_DY = 80
MISSING_BOX_W = 56
MISSING_BOX_H = 72
MISSING_BOX_DY = 28
MISSING_STEP = 64
PANGRAMS = ("the quick brown fox jumps over the lazy dog", "sphinx of black quartz judge my vow")

ALPHABETS: tuple[tuple[str, str], ...] = (
    ("A-Z", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    ("a-z", "abcdefghijklmnopqrstuvwxyz"),
    ("0-9", "0123456789"),
    ("punct", ".,!?'\"-;:()"),
)
FIXED_ROWS: tuple[Row, ...] = (
    ("pangram", PANGRAMS[0]),
    ("pangram", PANGRAMS[1]),
    ("stamping", "the the the"),
    ("stamping", "aaaa eeee llll"),
    ("stamping", "that there sees"),
    ("positional", "e eat tee"),
    ("quotes", "'the' don't \"the\" (the)"),
    ("features off", PANGRAMS[0], OFF3),
)
STYLE = "<style>.label { font: 10px sans-serif; fill: #888; }</style>"
_ALT_RE = re.compile(r"^(.+)\.alt(\d+)$")


# ---------- rows ----------


def _alphabet_rows(has: set[int]) -> list[Row]:
    rows: list[Row] = []
    for label, chars in ALPHABETS:
        text = "".join(c for c in chars if ord(c) in has)
        if text:
            rows.append((label, text))
    return rows


def _ring_rows(sizes: dict[str, int]) -> list[Row]:
    return [(f"ring {label}", label * sizes[label]) for label in sorted(sizes, key=label_key) if sizes[label] >= 2]


def default_rows(plan: GlyphPlan, samples: dict[str, LoadedSample]) -> list[Row]:
    from font_gen.select import is_eligible

    rows = _alphabet_rows(set(plan.cmap))
    rows += _ring_rows({label: len(ring) for (label, pos), ring in plan.rings.items() if pos is None and len(label) == 1})
    by_id = sorted(samples.values(), key=lambda ls: ls.sample.id)
    labels = {base_name(ls.sample.label): ls.sample.label for ls in by_id}
    for lig, (_, allowed) in plan.ligatures.items():
        for pos in POS_ORDER:
            if pos not in allowed:
                continue
            first = next((ls for ls in by_id if base_name(ls.sample.label) == lig and ls.position == pos and is_eligible(ls)), None)
            word = (first.sample.context.source_word if first is not None else "") or labels.get(lig, lig)
            rows.append((f"lig {lig} {pos}", word))
    return rows + list(FIXED_ROWS)


def proof_rows_from_font(font_bytes: bytes) -> list[Row]:
    """The `proof` verb's plan-free rows: `default_rows` recomputed from the binary alone, minus the ligature rows."""
    tt = TTFont(BytesIO(font_bytes))
    cmap = tt.getBestCmap()
    alts: dict[str, set[int]] = {}
    for name in tt.getGlyphOrder():
        m = _ALT_RE.match(name)
        if m:
            alts.setdefault(m.group(1), set()).add(int(m.group(2)))
    sizes: dict[str, int] = {}
    for cp, glyph in cmap.items():
        label = chr(cp)
        if glyph != base_name(label) or glyph not in alts:  # skip aliases (curly quotes, nbsp …) and ringless glyphs
            continue
        n = 1 + max(alts[glyph])
        if alts[glyph] == set(range(1, n)):
            sizes[label] = n
    return _alphabet_rows(set(cmap)) + _ring_rows(sizes) + list(FIXED_ROWS)


# ---------- SVG ----------


def _fmt(x: float) -> str:
    """Canonical number text: integers without a decimal point, otherwise at most three decimals."""
    if float(x).is_integer():
        return str(int(x))
    return f"{x:.3f}".rstrip("0").rstrip(".")


def render_proof(font_bytes: bytes, rows: Sequence[Row], missing: Sequence[str]) -> str:
    glyph_set = TTFont(BytesIO(font_bytes)).getGlyphSet()
    s = FONT_PX / 1000
    scale = _fmt(s)
    body: list[str] = []
    max_units = 0
    for r, row in enumerate(rows):
        label, text = row[0], row[1]
        features = row[2] if len(row) == 3 else None
        row_top = ROW_H * r
        body.append(f'  <text x="{LABEL_X}" y="{row_top + LABEL_DY}" class="label">{escape(label)}</text>')
        body.append(f'  <g transform="translate({LEFT}, {row_top + BASELINE_DY}) scale({scale}, -{scale})" fill-rule="nonzero">')
        x = 0
        for name, x_advance, x_offset, y_offset in shape_glyphs(font_bytes, text, features):
            pen = SVGPathPen(glyph_set)
            glyph_set[name].draw(pen)
            body.append(f'    <path transform="translate({str(int(x + x_offset))}, {str(int(y_offset))})" d="{pen.getCommands()}"/>')
            x += x_advance
        body.append("  </g>")
        max_units = max(max_units, x)
    width = max_units * s
    if missing:
        row_top = ROW_H * len(rows)
        body.append(f'  <text x="{LABEL_X}" y="{row_top + LABEL_DY}" class="label">missing</text>')
        for i, ch in enumerate(missing):
            bx, by = LEFT + MISSING_STEP * i, row_top + MISSING_BOX_DY
            body.append(f'  <rect x="{bx}" y="{by}" width="{MISSING_BOX_W}" height="{MISSING_BOX_H}" fill="none" stroke="red" stroke-width="2"/>')
            body.append(f'  <text x="{bx + MISSING_BOX_W // 2}" y="{by + MISSING_BOX_H // 2 + 12}" text-anchor="middle" '
                        f'font-family="sans-serif" font-size="32" fill="red">{escape(ch)}</text>')
        width = max(width, MISSING_STEP * (len(missing) - 1) + MISSING_BOX_W)
    total_w = _fmt(width + 2 * LEFT)
    height = ROW_H * (len(rows) + (1 if missing else 0)) + ROW_H
    head = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{height}" viewBox="0 0 {total_w} {height}">',
        f"  {STYLE}",
        '  <rect width="100%" height="100%" fill="white"/>',
    ]
    return "\n".join([*head, *body, "</svg>"]) + "\n"


# ---------- PNG ----------


def cairosvg_available() -> bool:
    try:
        import cairosvg  # noqa: F401
    except ImportError:
        return False
    return True


def render_png(svg_text: str) -> bytes:
    """Rasterise the proof SVG; `ImportError` (cairosvg absent) propagates to the caller (→ `proof_png_skipped`)."""
    import cairosvg

    return cairosvg.svg2png(bytestring=svg_text.encode("utf-8"))

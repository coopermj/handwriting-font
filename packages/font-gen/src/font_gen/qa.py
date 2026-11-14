"""QA findings: the one record type every stage reports problems with — and the HarfBuzz shaping helper the checks use."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from typing import Literal

import uharfbuzz as hb
from fontTools.ttLib import TTFont

Severity = Literal["error", "warn", "info"]


@dataclass(frozen=True)
class Finding:
    severity: Severity
    code: str
    message: str
    label: str | None = None
    sample_id: str | None = None
    glyph: str | None = None
    data: dict = field(default_factory=dict)


def shape_glyphs(font_bytes: bytes, text: str, features: dict[str, bool] | None = None) -> list[tuple[str, int, int, int]]:
    """Shape `text` with HarfBuzz: `(glyph name, x_advance, x_offset, y_offset)` per output glyph."""
    face = hb.Face(hb.Blob(font_bytes))
    font = hb.Font(face)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, features or {})
    order = TTFont(BytesIO(font_bytes)).getGlyphOrder()
    return [(order[info.codepoint], pos.x_advance, pos.x_offset, pos.y_offset)
            for info, pos in zip(buf.glyph_infos, buf.glyph_positions)]


def shape_names(font_bytes: bytes, text: str, features: dict[str, bool] | None = None) -> list[str]:
    return [name for name, _, _, _ in shape_glyphs(font_bytes, text, features)]

"""Build: assemble a CFF OpenType font (.otf) from outlines, metrics and generated features (spec §10)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO

from fontTools.fontBuilder import FontBuilder
from fontTools.misc.psCharStrings import T2CharString
from fontTools.misc.timeTools import timestampSinceEpoch
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib.tables._g_l_y_f import Glyph

from font_gen.metrics import VMetrics
from font_gen.nib import UPM
from font_gen.outline import Outline
from font_gen.select import GlyphPlan

VERSION = "0.1.0"
VENDOR = "HWFT"
FONT_REVISION = "1.000"
FS_SELECTION = 0x40 | 0x80  # REGULAR | USE_TYPO_METRICS
WEIGHT_CLASS = 400
OS2_VERSION = 4

_PS_STRIP = re.compile(r"[^A-Za-z0-9-]")


@dataclass(frozen=True)
class BuildMeta:
    family: str
    style: str
    digest12: str
    created_at: str
    version: str = VERSION

    @property
    def ps_name(self) -> str:
        return _PS_STRIP.sub("", self.family) + "-" + _PS_STRIP.sub("", self.style)


def head_timestamp(created_at: str) -> int:
    """ISO-8601 → seconds since 1904-01-01 (a trailing Z or a naive value is UTC)."""
    iso = created_at[:-1] + "+00:00" if created_at.endswith("Z") else created_at
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return timestampSinceEpoch(dt.timestamp())


def name_records(meta: BuildMeta) -> dict[str, str]:
    ps = meta.ps_name
    return {
        "familyName": meta.family,
        "styleName": meta.style,
        "uniqueFontIdentifier": f"{ps};font-gen {meta.version};{meta.digest12}",
        "version": f"Version {FONT_REVISION};font-gen {meta.version};store {meta.digest12}",
        "psName": ps,
        "fullName": f"{meta.family} {meta.style}",
    }


def charstrings(plan: GlyphPlan, outlines: dict[str, Outline], hm: dict[str, tuple[int, int]]) -> dict[str, T2CharString]:
    """One charstring per glyph; T2CharStringPen always writes an explicit width operand (spec §10)."""
    out: dict[str, T2CharString] = {}
    for name in plan.names():
        pen = T2CharStringPen(hm[name][0], None)
        outlines[name].draw(pen)
        out[name] = pen.getCharString()
    return out


def os2_values(vm: VMetrics) -> dict:
    return {
        "version": OS2_VERSION,
        "sTypoAscender": vm.ascender,
        "sTypoDescender": vm.descender,
        "sTypoLineGap": 0,
        "usWinAscent": vm.ascender,
        "usWinDescent": -vm.descender,
        "sxHeight": vm.x_height,
        "sCapHeight": vm.cap_height,
        "fsSelection": FS_SELECTION,
        "achVendID": VENDOR,
        "fsType": 0,
        "usWeightClass": WEIGHT_CLASS,
    }


def build_font(
    plan: GlyphPlan,
    outlines: dict[str, Outline],
    hmetrics: dict[str, tuple[int, int]],
    vmetrics: VMetrics,
    fea_text: str,
    meta: BuildMeta,
) -> bytes:
    """CFF .otf bytes; deterministic for identical inputs (fixed head timestamps)."""
    ts = head_timestamp(meta.created_at)
    names = plan.names()
    records = name_records(meta)
    fb = FontBuilder(UPM, isTTF=False)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap(plan.cmap)
    fb.setupCFF(
        meta.ps_name,
        {"FullName": records["fullName"], "FamilyName": meta.family, "Weight": "Regular", "ItalicAngle": 0,
         "version": FONT_REVISION},
        charstrings(plan, outlines, hmetrics),
        {"nominalWidthX": 0, "defaultWidthX": 0},
    )
    fb.setupHorizontalMetrics({name: hmetrics[name] for name in names})
    fb.setupHorizontalHeader(ascent=vmetrics.ascender, descent=vmetrics.descender)
    fb.setupNameTable(records)
    fb.setupOS2(**os2_values(vmetrics))
    os2 = fb.font["OS/2"]
    os2.recalcAvgCharWidth(fb.font)
    os2.recalcUnicodeRanges(fb.font)
    os2.recalcCodePageRanges(fb.font)
    fb.setupPost()
    fb.setupHead(created=ts, modified=ts)
    fb.addOpenTypeFeatures(fea_text)
    buf = BytesIO()
    fb.save(buf)
    return buf.getvalue()


def tt_glyphs(plan: GlyphPlan, outlines: dict[str, Outline]) -> dict[str, Glyph]:
    """One glyf-table Glyph per name: cubics → quadratics (max_err 1.0), outer contours reversed to clockwise."""
    out: dict[str, Glyph] = {}
    for name in plan.names():
        outline = outlines[name]
        pen = TTGlyphPen(None)
        outline.draw(Cu2QuPen(pen, max_err=1.0, reverse_direction=True))  # no contours: draws nothing, empty glyph
        glyph = pen.glyph()
        glyph.recalcBounds(None)  # TTGlyphPen.glyph() leaves xMin unset; FontBuilder computes bounds only in setupGlyf
        out[name] = glyph
    return out


def build_ttf(
    plan: GlyphPlan,
    outlines: dict[str, Outline],
    hmetrics: dict[str, tuple[int, int]],
    vmetrics: VMetrics,
    fea_text: str,
    meta: BuildMeta,
) -> bytes:
    """TrueType (glyf) .ttf bytes with the same features and metrics; deterministic like build_font."""
    ts = head_timestamp(meta.created_at)
    names = plan.names()
    glyphs = tt_glyphs(plan, outlines)
    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap(plan.cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(
        {name: (hmetrics[name][0], glyphs[name].xMin if glyphs[name].numberOfContours else 0) for name in names}
    )
    fb.setupHorizontalHeader(ascent=vmetrics.ascender, descent=vmetrics.descender)
    fb.setupNameTable(name_records(meta))
    fb.setupOS2(**os2_values(vmetrics))
    os2 = fb.font["OS/2"]
    os2.recalcAvgCharWidth(fb.font)
    os2.recalcUnicodeRanges(fb.font)
    os2.recalcCodePageRanges(fb.font)
    fb.setupPost()
    fb.setupHead(created=ts, modified=ts)
    fb.setupMaxp()
    fb.addOpenTypeFeatures(fea_text)
    buf = BytesIO()
    fb.save(buf)
    return buf.getvalue()

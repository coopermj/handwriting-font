import io
import math

from fontTools.misc.timeTools import timestampSinceEpoch
from fontTools.pens.areaPen import AreaPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont

from font_gen.build import BuildMeta, build_font, build_ttf, head_timestamp
from font_gen.outline import Outline
from font_gen.qa import shape_names


def _tt(data):
    return TTFont(io.BytesIO(data))


def test_reload_glyph_order_cmap_and_hmtx(fixture_otf, fixture_plan, fixture_hm):
    tt = _tt(fixture_otf)
    assert tt.getGlyphOrder() == fixture_plan.names()
    cs = tt["CFF "].cff.topDictIndex[0].CharStrings
    assert len(cs.keys()) == len(fixture_plan.names())
    cmap = tt.getBestCmap()
    assert cmap[ord("a")] == "a" and cmap[0x20] == "space" and cmap[0xA0] == "space"
    assert all(".alt" not in n and ".fina" not in n and "_" not in n for n in cmap.values())
    for name, (adv, lsb) in fixture_hm.items():
        assert tt["hmtx"][name] == (adv, lsb), name


def test_charstrings_match_outlines_and_carry_explicit_width(fixture_otf, fixture_plan, fixture_outlines, fixture_hm):
    tt = _tt(fixture_otf)
    top = tt["CFF "].cff.topDictIndex[0]
    assert top.Private.nominalWidthX == 0 and top.Private.defaultWidthX == 0
    gs = tt.getGlyphSet()
    for name in fixture_plan.names():
        cs = top.CharStrings[name]
        cs.decompile()
        assert cs.program[0] == fixture_hm[name][0]         # explicit width operand, nominalWidthX == 0
        bp = BoundsPen(gs)
        gs[name].draw(bp)
        b = bp.bounds                                       # Outline.bounds is floor/ceil of the exact curve bounds
        got = None if b is None else (math.floor(b[0]), math.floor(b[1]), math.ceil(b[2]), math.ceil(b[3]))
        assert got == fixture_outlines[name].bounds, name


def test_os2_hhea_head_name_post_and_gdef(fixture_otf, fixture_vm, fixture_meta):
    tt = _tt(fixture_otf)
    os2, hhea, head = tt["OS/2"], tt["hhea"], tt["head"]
    assert os2.version == 4 and os2.sTypoAscender == hhea.ascent == fixture_vm.ascender
    assert os2.sTypoDescender == hhea.descent == fixture_vm.descender and os2.sTypoLineGap == hhea.lineGap == 0
    assert os2.usWinAscent == fixture_vm.ascender and os2.usWinDescent == -fixture_vm.descender
    assert os2.fsSelection & 0xC0 == 0xC0 and os2.achVendID == "HWFT" and os2.fsType == 0 and os2.usWeightClass == 400
    assert os2.sxHeight == fixture_vm.x_height and os2.sCapHeight == fixture_vm.cap_height and os2.xAvgCharWidth > 0
    assert os2.ulUnicodeRange1 & 1 and os2.ulCodePageRange1 & 1      # recalcUnicodeRanges: Basic Latin; recalcCodePageRanges: Latin 1
    ts = timestampSinceEpoch(0)
    assert head.created == head.modified == ts == head_timestamp("1970-01-01T00:00:00Z") and head.unitsPerEm == 1000
    assert head_timestamp("1970-01-01T00:00:00") == ts and head_timestamp("1970-01-01T01:00:00+01:00") == ts
    name = tt["name"]
    assert name.getDebugName(6) == "FixtureHand-Regular" and name.getDebugName(1) == "Fixture Hand"
    assert name.getDebugName(5) == "Version 1.000;font-gen 0.1.0;store 000000000000"
    assert name.getDebugName(3) == "FixtureHand-Regular;font-gen 0.1.0;000000000000"
    assert tt["post"].formatType == 3.0
    gdef = tt["GDEF"].table
    assert gdef.GlyphClassDef.classDefs["f_i"] == 2 and gdef.GlyphClassDef.classDefs["a"] == 1
    carets = {gdef.LigCaretList.Coverage.glyphs[i]: [c.Coordinate for c in g.CaretValue] for i, g in enumerate(gdef.LigCaretList.LigGlyph)}
    assert len(carets["f_i"]) == 1 and len(carets["t_i_o_n"]) == 3


def test_byte_identical_rebuild_and_shaping(fixture_otf, fixture_plan, fixture_outlines, fixture_hm, fixture_vm, fixture_fea, fixture_meta):
    again = build_font(fixture_plan, fixture_outlines, fixture_hm, fixture_vm, fixture_fea, fixture_meta)
    assert again == fixture_otf
    assert shape_names(fixture_otf, "afia")[1] == "f_i.alt1"       # fi is medial-only in the fixture; the ligature glyph cycles
    assert len(set(shape_names(fixture_otf, "aaa"))) == 3


def test_empty_outline_glyph_survives_build(fixture_plan, fixture_outlines, fixture_hm, fixture_vm, fixture_fea, fixture_meta):
    outlines = dict(fixture_outlines)
    outlines["a.alt1"] = Outline.empty()
    hm = dict(fixture_hm)
    hm["a.alt1"] = (hm["a.alt1"][0], 0)
    data = build_font(fixture_plan, outlines, hm, fixture_vm, fixture_fea, fixture_meta)
    tt = _tt(data)
    rec = RecordingPen()
    tt.getGlyphSet()["a.alt1"].draw(rec)
    assert rec.value == [] and tt["hmtx"]["a.alt1"] == (hm["a.alt1"][0], 0)
    bp = BoundsPen(tt.getGlyphSet())
    tt.getGlyphSet()["a.alt1"].draw(bp)
    assert bp.bounds is None


def test_ttf_has_glyf_clockwise_outers_post_2_and_shapes_identically(fixture_otf, fixture_plan, fixture_outlines, fixture_hm, fixture_vm, fixture_fea, fixture_meta):
    ttf = build_ttf(fixture_plan, fixture_outlines, fixture_hm, fixture_vm, fixture_fea, fixture_meta)
    tt = _tt(ttf)
    assert "glyf" in tt and "CFF " not in tt and tt["post"].formatType == 2.0
    assert tt.getGlyphOrder() == fixture_plan.names() and tt["maxp"].numGlyphs == len(fixture_plan.names())
    gs = tt.getGlyphSet()
    ap = AreaPen(gs)
    gs["a"].draw(ap)
    assert ap.value < 0                                    # clockwise outer for TrueType
    for name, (adv, _) in fixture_hm.items():
        assert tt["hmtx"][name][0] == adv
        assert tt["hmtx"][name][1] == (tt["glyf"][name].xMin if tt["glyf"][name].numberOfContours else 0)
    assert tt["head"].created == _tt(fixture_otf)["head"].created
    for text in ("the the the", "fi", "aaaaaaaa", "nation"):
        assert shape_names(ttf, text) == shape_names(fixture_otf, text)
    assert build_ttf(fixture_plan, fixture_outlines, fixture_hm, fixture_vm, fixture_fea, fixture_meta) == ttf

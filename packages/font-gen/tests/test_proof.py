import re

import pytest
from conftest import assert_golden

from font_gen.proof import OFF3, cairosvg_available, default_rows, render_proof


def test_default_rows_order_and_content(fixture_plan, fixture_samples):
    rows = default_rows(fixture_plan, fixture_samples)
    labels = [r[0] for r in rows]
    assert labels[0] == "A-Z" and rows[0][1] == "Q" and labels[1] == "a-z" and "0-9" not in labels
    assert ("ring a", "a" * 6) in rows and ("ring z", "zz") in rows and not any(l == "ring Q" for l in labels)
    lig = [r for r in rows if r[0].startswith("lig t_i_o_n")]
    assert len(lig) == 1 and lig[0][0] == "lig t_i_o_n fina" and "tion" in lig[0][1]
    assert ("stamping", "the the the") in rows and ("positional", "e eat tee") in rows
    assert ("quotes", "'the' don't \"the\" (the)") in rows
    assert rows[-1] == ("features off", "the quick brown fox jumps over the lazy dog", OFF3)


def test_svg_golden_one_path_per_glyph_and_red_box(fixture_otf, fixture_plan, fixture_samples, goldens, update_goldens):
    rows = default_rows(fixture_plan, fixture_samples)
    svg = render_proof(fixture_otf, rows, ["k"])
    assert_golden(goldens / "proof.svg", svg, update_goldens)
    assert svg == render_proof(fixture_otf, rows, ["k"])
    assert svg.count("<path") == sum(len(shape_len(fixture_otf, r)) for r in rows)
    assert 'fill="red"' in svg and svg.count("<g ") == len(rows)


def shape_len(font, row):
    from font_gen.qa import shape_glyphs
    return shape_glyphs(font, row[1], row[2] if len(row) == 3 else None)


def test_quote_row_and_notdef_render(fixture_otf):
    svg = render_proof(fixture_otf, [("quotes", "'the'"), ("missing", "k")], [])   # ', h, e, k are not in the fixture cmap
    assert svg.count("<path") == 6 and "<text" in svg and ".notdef" not in svg
    assert re.search(r'<g transform="translate\(40, 80\) scale\(0\.064, -0\.064\)" fill-rule="nonzero">', svg)
    assert re.search(r'<g transform="translate\(40, 190\) scale\(0\.064, -0\.064\)"', svg)       # second row: ROW_H + 80
    assert re.search(r'<path transform="translate\(0, 0\)" d="', svg)                              # first glyph of a row sits at the origin


def test_path_lines_depend_only_on_the_row_text(fixture_otf):
    one = render_proof(fixture_otf, [("x", "aa")], [])
    two = render_proof(fixture_otf, [("y", "zz"), ("x", "aa")], [])
    paths = lambda s: [line for line in s.splitlines() if "<path" in line]
    assert paths(one) == paths(two)[-2:]


def test_text_rows_append_and_replace_are_plain_data(fixture_otf, fixture_plan, fixture_samples):
    base = default_rows(fixture_plan, fixture_samples)
    extra = [("text 1", "hello"), ("text 2", "world")]
    assert render_proof(fixture_otf, base + extra, []).count("<path") > render_proof(fixture_otf, base, []).count("<path")
    assert render_proof(fixture_otf, extra, []).count("<path") == 10


def test_rows_from_the_bare_binary_are_the_default_rows_minus_ligatures(fixture_otf, fixture_plan, fixture_samples):
    from font_gen.proof import proof_rows_from_font
    expected = [r for r in default_rows(fixture_plan, fixture_samples) if not r[0].startswith("lig ")]
    assert proof_rows_from_font(fixture_otf) == expected
    assert ("ring a", "aaaaaa") in expected and ("ring z", "zz") in expected


def test_png_branch_requires_cairosvg(fixture_otf):
    pytest.importorskip("cairosvg")
    from font_gen.proof import render_png
    assert cairosvg_available()
    assert render_png(render_proof(fixture_otf, [("x", "a")], []))[:8] == b"\x89PNG\r\n\x1a\n"

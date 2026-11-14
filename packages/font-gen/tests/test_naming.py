from font_gen.naming import CMAP_ALIASES, base_name, char_name, glyph_name, label_key, strip_suffixes, validate
from font_gen.synth import default_targets


def test_agl_names_for_every_default_single_and_fallback():
    expect = {"a": "a", "A": "A", "0": "zero", ".": "period", ",": "comma", "!": "exclam", "?": "question",
              "'": "quotesingle", '"': "quotedbl", "-": "hyphen", ";": "semicolon", ":": "colon",
              "(": "parenleft", ")": "parenright"}
    for label, name in expect.items():
        assert base_name(label) == name
    for t in default_targets():
        if len(t.label) == 1:
            assert not base_name(t.label)[0].isdigit()
    assert char_name("☃") == "uni2603"


def test_ligature_names_join_components_with_underscore():
    assert base_name("fi") == "f_i" and base_name("ffi") == "f_f_i" and base_name("tion") == "t_i_o_n"
    assert base_name("Sch") == "S_c_h" and base_name("Th") == "T_h"


def test_suffix_grammar_and_lengths():
    assert glyph_name("a", None, 0) == "a"
    assert glyph_name("a", None, 3) == "a.alt3"
    assert glyph_name("a", "init", 0) == "a.init"
    assert glyph_name("a", "init", 2) == "a.init.alt2"
    assert glyph_name("tion", "fina", 0) == "t_i_o_n.fina"
    assert glyph_name("Sch", None, 1) == "S_c_h.alt1"
    assert glyph_name("'", None, 6) == "quotesingle.alt6"
    assert len(glyph_name("Sch", "fina", 6)) == 15 and len(glyph_name("Sch", "fina", 12)) == 16
    assert strip_suffixes("a.init.alt2") == "a" and strip_suffixes("t_i_o_n.fina") == "t_i_o_n" and strip_suffixes("e.fina.alt12") == "e"


def test_validate_grammar_ring_bounds_and_uniqueness():
    assert validate([".notdef", "space", "a", "a.alt6", "S_c_h.fina.alt6"], ring_n=7) == []
    assert validate(["a.alt12"], ring_n=13) == []
    bad = validate(["a.alt7", "a.alt13", "1abc", "a b", "x" * 32, "dup", "dup"], ring_n=7)
    assert all(f.code == "glyph_name_invalid" and f.severity == "error" for f in bad)
    assert [f.glyph for f in bad] == ["a.alt7", "a.alt13", "1abc", "a b", "x" * 32, "dup"]
    assert [f.glyph for f in validate(["a.alt12"], ring_n=7)] == ["a.alt12"]


def test_label_key_and_aliases():
    assert label_key("fi") == (102, 105) and sorted(["b", "A", "a"], key=label_key) == ["A", "a", "b"]
    assert CMAP_ALIASES[0x2019] == "quotesingle" and CMAP_ALIASES[0x00A0] == "space" and CMAP_ALIASES[0x2013] == "hyphen"

import pytest
from conftest import CYCLE_SPEC, LIGA2_SPEC, LIGA_SPEC, PUNCT_SPEC, stub_font_from_spec

from font_gen.naming import strip_suffixes
from font_gen.qa import shape_names

OFF3 = {"calt": False, "liga": False, "clig": False}


@pytest.fixture(scope="module")
def cycle_font(tmp_path_factory):
    return stub_font_from_spec(tmp_path_factory.mktemp("cycle"), CYCLE_SPEC)[0]


@pytest.fixture(scope="module")
def punct_font(tmp_path_factory):
    return stub_font_from_spec(tmp_path_factory.mktemp("punct"), PUNCT_SPEC)[0]


@pytest.fixture(scope="module")
def liga_font(tmp_path_factory):
    return stub_font_from_spec(tmp_path_factory.mktemp("liga"), LIGA_SPEC)[0]


@pytest.fixture(scope="module")
def liga2_font(tmp_path_factory):
    return stub_font_from_spec(tmp_path_factory.mktemp("liga2"), LIGA2_SPEC)[0]


@pytest.fixture(scope="module")
def word_font(tmp_path_factory):
    return stub_font_from_spec(tmp_path_factory.mktemp("word"), CYCLE_SPEC, cycle_mode="word")[0]


def stripped(font, text, features=None):
    return [strip_suffixes(n) for n in shape_names(font, text, features)]


@pytest.mark.parametrize("text,expect", [
    ("aaaaaaaa", ["a.init", "a.alt1", "a.alt2", "a.alt3", "a.alt4", "a.alt5", "a.alt6", "a.fina"]),
    ("abab", ["a.init", "b.alt1", "a.alt2", "b.alt3"]),
    ("the the the", ["t", "h.alt1", "e.fina.alt2", "space", "t.alt3", "h.alt4", "e.fina.alt1", "space", "t.alt2", "h.alt3", "e.fina"]),
    ("a a", ["a", "space", "a.alt1"]),
    ("a. a", ["a", "period", "space", "a.alt1"]),
    ("a...a", ["a", "period", "period", "period", "a"]),
    ("zzzz", ["z", "z.alt1", "z", "z.alt1"]),
    ("a", ["a"]),
    ("ae", ["a.init", "e.fina.alt1"]),
    ("the", ["t", "h.alt1", "e.fina.alt2"]),
    ("'the'", ["quotesingle", "t.alt1", "h.alt2", "e.alt3", "quotesingle"]),
    ("aaa", ["a.init", "a.alt1", "a.fina.alt2"]),      # the medial guard: without it the middle a becomes a.fina.alt1
    ("bab", ["b", "a.alt1", "b.alt2"]),
])
def test_cycling_positions_and_transparent_skipping(cycle_font, text, expect):
    assert shape_names(cycle_font, text) == expect


@pytest.mark.parametrize("text,expect", [
    ("a.a", ["a", "period.alt1", "a.alt2"]),                        # the period cycles and steps the counter
    ("a..a", ["a", "period.alt1", "period", "a.alt1"]),             # slot 2 of a 2-ring is the base again: modulus re-sync
    ("a...a", ["a", "period.alt1", "period", "period.alt1", "a.alt2"]),
    ("a. a", ["a", "period.alt1", "space", "a.alt2"]),
    (".a.", ["period", "a.alt1", "period"]),
    ("a.a.a", ["a", "period.alt1", "a.alt2", "period.alt1", "a.alt2"]),
])
def test_punctuation_ring_cycles_and_steps_the_counter(punct_font, text, expect):
    assert shape_names(punct_font, text) == expect


def test_feature_switches(cycle_font):
    assert shape_names(cycle_font, "fi") == ["f_i"] and shape_names(cycle_font, "ffi") == ["f_f_i"]
    assert shape_names(cycle_font, "fi", {"liga": False}) == ["f_i"]                       # clig still forms it
    assert shape_names(cycle_font, "ffi", {"liga": False, "clig": False}) == ["f", "f.alt1", "i.alt2"]
    assert shape_names(cycle_font, "aaa", {"calt": False}) == ["a", "a", "a"]
    assert shape_names(cycle_font, "aaa", OFF3) == ["a", "a", "a"] and shape_names(cycle_font, "fi", OFF3) == ["f", "i"]


def test_cycle_mode_word_restarts_at_spaces(word_font):
    assert shape_names(word_font, "the the") == ["t", "h.alt1", "e.fina.alt2", "space", "t", "h.alt1", "e.fina.alt2"]


@pytest.mark.parametrize("text,expect", [
    ("ffi", ["f_f_i"]), ("fi", ["f_i"]), ("str", ["s_t", "r"]), ("st", ["s_t"]),
    ("string", ["s_t", "r", "i_n_g"]),
    ("office", ["o", "f", "f_i", "c", "e"]), ("affix", ["a", "f", "f_i", "x"]),
    ("asthma", ["a", "s", "t_h", "m", "a"]), ("Bingham", ["B", "i", "n", "g_h", "a", "m"]),
    ("angha", ["a", "n", "g_h", "a"]), ("agha", ["a", "g_h", "a"]),
    ("best", ["b", "e", "s_t"]),
    ("nation", ["n", "a", "t_i_o_n"]), ("nations", ["n", "a", "t", "i", "o", "n", "s"]),
    ("sing", ["s", "i_n_g"]), ("singer", ["s", "i", "n", "g", "e", "r"]),
])
def test_ligature_tiers_restriction_and_no_shadowing(liga_font, text, expect):
    assert stripped(liga_font, text) == expect


def test_ligature_glyphs_cycle_and_position_as_units(liga_font):
    assert shape_names(liga_font, "nation")[1] == "a.alt1"
    assert shape_names(liga_font, "sing")[1] == "i_n_g.alt1"


@pytest.mark.parametrize("text,expect", [
    ("staff", ["s", "t", "a", "f_f"]), ("stiff", ["s", "t", "i", "f_f"]), ("best", ["b", "e", "s_t"]),
])
def test_restricted_ligatures_fire_only_where_allowed(liga2_font, text, expect):
    assert stripped(liga2_font, text) == expect


def test_isolated_letter_with_only_an_init_ring_never_takes_init(liga2_font):
    assert shape_names(liga2_font, "a") == ["a"]
    assert shape_names(liga2_font, "ab") == ["a.init", "b.alt1"]   # a.init is in @ST0, so b (a 3-ring) steps (§9.6)

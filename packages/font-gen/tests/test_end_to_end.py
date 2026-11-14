import io
import time

from fontTools.ttLib import TTFont

from font_gen.naming import strip_suffixes
from font_gen.qa import shape_names
from font_gen.run import BuildOptions, run_build


def test_store_to_font_to_shaping_round_trip(e2e_store):
    t0 = time.perf_counter()
    result = run_build(e2e_store, BuildOptions(ttf=True))
    elapsed = time.perf_counter() - t0
    assert elapsed < 90, elapsed
    assert result.otf is not None and result.ttf is not None and result.report.has_errors is False
    tt = TTFont(io.BytesIO(result.otf))
    assert tt.getGlyphOrder() == result.plan.names() and "k" not in tt.getGlyphOrder()
    assert [strip_suffixes(n) for n in shape_names(result.otf, "nation")] == ["n", "a", "t_i_o_n"]
    assert shape_names(result.otf, "the the the")[:3] != shape_names(result.otf, "the the the")[4:7]
    assert shape_names(result.ttf, "the the the") == shape_names(result.otf, "the the the")
    assert "<path" in result.proof_svg and 'fill="red"' in result.proof_svg       # missing target k
    again = run_build(e2e_store, BuildOptions(ttf=True))
    assert again.otf == result.otf and again.ttf == result.ttf and again.fea == result.fea


def test_feature_toggles_on_the_real_font(e2e_store):
    result = run_build(e2e_store, BuildOptions())
    off = {"calt": False, "liga": False, "clig": False}
    for ch in "atosb":
        assert shape_names(result.otf, ch, off) == [strip_suffixes(shape_names(result.otf, ch)[0])]
    assert shape_names(result.otf, "aa", {"calt": False}) == ["a", "a"]

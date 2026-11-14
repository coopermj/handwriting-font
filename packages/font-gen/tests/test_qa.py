import io
import json

from fontTools.misc.timeTools import timestampSinceEpoch
from fontTools.ttLib import TTFont

from font_gen.build import build_ttf
from font_gen.qa import (ERROR_CODES, INFO_CODES, WARN_CODES, Expectation, Finding, QAReport, check_expectation,
                         coverage_rows, exit_code, probe_letter, report_json, self_check, severity_of,
                         shaping_expectations, summarize)

TS = timestampSinceEpoch(0)


def test_severity_policy_is_the_spec_table():
    assert ERROR_CODES == {"empty_font", "store_invalid", "glyph_name_invalid", "feature_compile_failed", "reload_failed",
                           "metrics_inconsistent_binary", "shaping_check_failed", "output_exists"}
    assert len(WARN_CODES) == 22 and {"target_no_samples", "ring_singleton", "proof_png_failed", "baseline_nonzero"} <= WARN_CODES
    assert len(INFO_CODES) == 17 and "output_path_normalized" in INFO_CODES and {"nib_width_assumed", "proof_png_skipped", "font_stats", "ligature_overlap"} <= INFO_CODES
    assert not (ERROR_CODES & WARN_CODES) and not (WARN_CODES & INFO_CODES)
    assert severity_of("empty_font") == "error" and severity_of("ring_short") == "info"


def _report(*findings):
    return QAReport(1, "0" * 64, "F", "1970-01-01T00:00:00Z", 7, tuple(findings), (), {"glyphs": 3}, ())


def test_exit_code_mapping():
    warn = Finding("warn", "ring_singleton", "m", glyph="Q")
    err = Finding("error", "empty_font", "m")
    assert exit_code(_report(), strict=False) == 0 and exit_code(_report(warn), strict=False) == 0
    assert exit_code(_report(warn), strict=True) == 2 and exit_code(_report(err, warn), strict=True) == 1
    assert exit_code(_report(err), strict=False) == 1


def test_report_json_sorted_and_summary_lines():
    rep = _report(Finding("info", "ring_short", "n=2", label="z", data={"n": 2}), Finding("warn", "ring_singleton", "stamped", glyph="Q"))
    text = report_json(rep)
    data = json.loads(text)
    assert data["em_convention_version"] == 1 and [f["code"] for f in data["findings"]] == ["ring_singleton", "ring_short"]
    assert list(data) == sorted(data)
    out = summarize(rep, "F.otf")
    assert out.splitlines()[0] == "Wrote F.otf: 3 glyphs, 0 ligatures, ring 7; QA: 0 errors, 1 warnings, 1 infos → F.qa.json"
    assert "ring_singleton" in out and "ring_short" not in out
    assert summarize(rep, None).splitlines()[0] == "No font written: QA: 0 errors, 1 warnings, 1 infos"
    written = QAReport(1, "0" * 64, "F", "1970-01-01T00:00:00Z", 7, (), (), {"glyphs": 3, "ligatures": 5}, ("out/F.fea", "out/F.otf", "out/F.qa.json"))
    assert summarize(written, "out/F.otf").splitlines()[0].endswith("5 ligatures, ring 7; QA: 0 errors, 0 warnings, 0 infos → out/F.qa.json")


def test_coverage_rows_carry_both_counts(e2e_store):
    from font_gen.load import load_store
    from font_gen.select import plan_glyphs
    loaded = load_store(e2e_store)
    plan, _ = plan_glyphs(loaded)
    rows = {r.label: r for r in coverage_rows(loaded.coverage, plan.eligible)}
    assert rows["b"].accepted == 12 and rows["b"].eligible == 2 and rows["b"].met is True and rows["b"].met_eligible is False
    assert rows["k"].eligible == 0 and rows["k"].met_eligible is False


def test_self_check_passes_on_the_fixture_font(fixture_otf, fixture_plan, fixture_outlines, fixture_hm, fixture_vm):
    assert self_check(fixture_otf, fixture_plan, fixture_outlines, fixture_hm, fixture_vm, TS) == []


def test_self_check_passes_on_ttf(fixture_plan, fixture_outlines, fixture_hm, fixture_vm, fixture_fea, fixture_meta):
    ttf = build_ttf(fixture_plan, fixture_outlines, fixture_hm, fixture_vm, fixture_fea, fixture_meta)
    assert self_check(ttf, fixture_plan, fixture_outlines, fixture_hm, fixture_vm, TS, is_ttf=True) == []


def _resave(data, mutate):
    # recalcTimestamp=False: the default would stamp head.modified with "now" and trip self-check step 3 as well
    tt = TTFont(io.BytesIO(data), recalcTimestamp=False, recalcBBoxes=False)
    mutate(tt)
    buf = io.BytesIO()
    tt.save(buf)
    return buf.getvalue()


def test_self_check_catches_tampered_hmtx_and_stripped_gsub(fixture_otf, fixture_plan, fixture_outlines, fixture_hm, fixture_vm):
    def tamper(tt):
        adv, lsb = tt["hmtx"]["a"]
        tt["hmtx"]["a"] = (adv + 7, lsb)
    bad = _resave(fixture_otf, tamper)
    assert {f.code for f in self_check(bad, fixture_plan, fixture_outlines, fixture_hm, fixture_vm, TS)} == {"metrics_inconsistent_binary"}

    stripped = _resave(fixture_otf, lambda tt: tt.__delitem__("GSUB"))
    codes = {f.code for f in self_check(stripped, fixture_plan, fixture_outlines, fixture_hm, fixture_vm, TS)}
    assert codes == {"shaping_check_failed"}
    assert {f.code for f in self_check(b"not a font", fixture_plan, fixture_outlines, fixture_hm, fixture_vm, TS)} == {"reload_failed"}


def test_generated_expectations_cover_the_plan_and_pass(fixture_otf, fixture_plan):
    exps = shaping_expectations(fixture_plan)
    texts = [e.text for e in exps]
    assert probe_letter(fixture_plan, ("f", "i")) == "z"            # first preference letter in the cmap that extends no ligature
    assert "zfiz" in texts and "zfi" in texts and "fiz" in texts and "fi" in texts
    assert any(e.mode == "noalt" and e.text == "za" and e.expect == ("z", "a.fina") for e in exps)
    assert any(e.mode == "distinct" and e.text == "aa" for e in exps)
    assert any(e.mode == "ends_distinct" and e.text == "a a" for e in exps)
    assert any(e.features == {"calt": False, "liga": False, "clig": False} and e.text == "a" and e.expect == ("a",) for e in exps)
    assert {e.text for e in exps if e.features} == {chr(cp) for cp in fixture_plan.cmap}   # every cmap'd character, space included
    overlap = {e.text: e.expect for e in exps if e.note.startswith("overlap ")}
    assert overlap == {"ffi": ("f", "f", "i"), "fing": ("f", "i_n_g"), "stion": ("s", "t_i_o_n")}   # the fixture's three overlap pairs
    assert not any(e.text in ("a. a", "a...a") for e in exps)                                       # period is not cmap'd in the fixture
    assert all(all(ord(ch) in fixture_plan.cmap for ch in e.text) for e in exps)
    failures = [(e.text, check_expectation(fixture_otf, e)) for e in exps if check_expectation(fixture_otf, e)]
    assert failures == []
    assert check_expectation(fixture_otf, Expectation("zfiz", ("z", "f", "i", "z"), "stripped", None, "wrong")) is not None  # fi is medial: z f_i z
    assert check_expectation(fixture_otf, Expectation("fi", ("f", "i"), "stripped", None, "right")) is None


def test_self_check_accepts_an_empty_outline_glyph(fixture_plan, fixture_outlines, fixture_hm, fixture_vm, fixture_fea, fixture_meta):
    from font_gen.build import build_font
    from font_gen.outline import Outline
    outlines = dict(fixture_outlines)
    outlines["a.alt1"] = Outline.empty()
    hm = dict(fixture_hm)
    hm["a.alt1"] = (hm["a.alt1"][0], 0)
    data = build_font(fixture_plan, outlines, hm, fixture_vm, fixture_fea, fixture_meta)
    assert self_check(data, fixture_plan, outlines, hm, fixture_vm, TS) == []   # Review Focus 2: BoundsPen None == Outline.bounds None


def test_self_check_tolerates_a_plan_without_calt(tmp_path):
    from conftest import stub_font_from_spec
    from font_gen.metrics import VMetrics
    from font_gen.outline import Outline, notdef_outline
    font, plan = stub_font_from_spec(tmp_path, {"a": {"medi": 1}, "b": {"medi": 1}})
    outlines = {n: Outline.from_recording([("moveTo", ((50, 0),)), ("lineTo", ((450, 0),)), ("lineTo", ((450, 500),)), ("lineTo", ((50, 500),)), ("closePath", ())]) for n in plan.names()}
    outlines["space"] = Outline.empty()
    hm = {n: (500, 50 if n != "space" else 0) for n in plan.names()}
    findings = self_check(font, plan, outlines, hm, VMetrics(800, -200, 0, 450, 700), TTFont(io.BytesIO(font))["head"].created)
    assert [f.code for f in findings] == []


def test_period_rows_need_a_transparent_period(tmp_path):
    # a cycling period steps the counter itself: with a 2-variant letter `a. a` shapes to `a period.alt1 space a`
    # (first == last) and `a...a` does not reset, so neither §9.9 period row is generated unless the period is transparent
    from conftest import stub_font_from_spec
    for n in (2, 3):
        font, plan = stub_font_from_spec(tmp_path / f"p{n}", {"a": {"medi": 2}, ".": {"isol": n}})
        assert "period" not in plan.transparent
        exps = shaping_expectations(plan)
        texts = [e.text for e in exps]
        assert "a a" in texts and "a. a" not in texts and "a...a" not in texts
        assert [(e.text, check_expectation(font, e)) for e in exps if check_expectation(font, e)] == []
    font, plan = stub_font_from_spec(tmp_path / "p1", {"a": {"medi": 2}, ".": {"isol": 1}})
    assert "period" in plan.transparent
    exps = shaping_expectations(plan)
    texts = [e.text for e in exps]
    assert "a. a" in texts and "a...a" in texts
    assert [(e.text, check_expectation(font, e)) for e in exps if check_expectation(font, e)] == []


def test_word_cycle_mode_expects_the_counter_to_restart_at_every_word(fixture_plan):
    word = shaping_expectations(fixture_plan, "word")
    assert any(e.mode == "ends_equal" and e.text == "a a" for e in word)
    assert not any(e.mode == "ends_distinct" for e in word)   # no @TRANSPARENT rules in --cycle-mode word


def test_nested_ligature_labels_still_get_all_four_probe_rows(tmp_path):
    # ruling R3: fi inside ffi and ng inside ing are not "other" ligatures for the probe, so every ligature is
    # checked in each allowed and disallowed position on the built font (spec §11.5), not only in isolation
    from conftest import LIGA_SPEC, stub_font_from_spec
    font, plan = stub_font_from_spec(tmp_path, LIGA_SPEC)
    exps = shaping_expectations(plan)
    for lig, probe in (("f_f_i", "x"), ("i_n_g", "x"), ("s_t_r", "x")):
        components, _ = plan.ligatures[lig]
        label = "".join(components)
        assert probe_letter(plan, components) == probe, lig
        rows = {e.note.rsplit(" ", 1)[1]: e.text for e in exps if e.note.startswith(f"ligature {label} ")}
        assert rows == {"init": label + probe, "medi": probe + label + probe, "fina": probe + label, "isol": label}, lig
    assert [(e.text, check_expectation(font, e)) for e in exps if check_expectation(font, e)] == []


def test_expectation_outside_the_cmap_raises_value_error(fixture_plan, monkeypatch):
    import pytest

    import font_gen.qa as qa
    real = qa._probe_words
    monkeypatch.setattr(qa, "_probe_words", lambda probe, core: {**real(probe, core), "isol": core + "☃"})
    with pytest.raises(ValueError, match="uses a character outside the cmap"):
        qa.shaping_expectations(fixture_plan)


def _swap_calt_sections(fea: str) -> str:
    """Define the variant-cycling lookups (STEP*, CYCLE) before the positional ones (TO_*, POS)."""
    head, rest = fea.split("\n# ---------- positional forms ----------\n")
    pos, rest = rest.split("\n# ---------- variant cycling ----------\n")
    cyc, tail = rest.split("\n# ---------- features ----------\n")
    return (head + "\n# ---------- variant cycling ----------\n" + cyc + "\n# ---------- positional forms ----------\n" + pos
            + "\n# ---------- features ----------\n" + tail)


def test_gsub_check_requires_pos_before_cycle_within_calt(fixture_otf, fixture_plan, fixture_fea):
    from conftest import build_stub_font

    from font_gen.qa import _check_gsub
    assert _check_gsub(TTFont(io.BytesIO(fixture_otf)), fixture_plan) == []
    assert _check_gsub(TTFont(io.BytesIO(build_stub_font(fixture_plan, fixture_fea))), fixture_plan) == []
    swapped = build_stub_font(fixture_plan, _swap_calt_sections(fixture_fea))
    problems = _check_gsub(TTFont(io.BytesIO(swapped)), fixture_plan)
    assert len(problems) == 1 and "POS lookups" in problems[0] and "precede" in problems[0]


def test_shape_glyphs_caches_the_parsed_font_per_bytes(fixture_otf, monkeypatch):
    import font_gen.qa as qa
    qa._SHAPER_CACHE.clear()
    calls = []
    real = qa._load_shaper
    monkeypatch.setattr(qa, "_load_shaper", lambda data: calls.append(len(data)) or real(data))
    first = qa.shape_names(fixture_otf, "aa")
    assert qa.shape_names(bytes(fixture_otf), "aa") == first and qa.shape_names(fixture_otf, "fi") and len(calls) == 1
    other = _resave(fixture_otf, lambda tt: setattr(tt["head"], "fontRevision", 1.5))
    assert other != fixture_otf
    assert qa.shape_names(other, "aa") == first and len(calls) == 2
    for i in range(qa._SHAPER_CACHE_SIZE):  # bounded: the oldest font is evicted
        qa.shape_names(_resave(fixture_otf, lambda tt, r=i: setattr(tt["head"], "fontRevision", 2.0 + r)), "a")
    assert len(qa._SHAPER_CACHE) == qa._SHAPER_CACHE_SIZE and len(calls) == 2 + qa._SHAPER_CACHE_SIZE
    qa.shape_names(fixture_otf, "a")
    assert len(calls) == 3 + qa._SHAPER_CACHE_SIZE

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
    assert len(INFO_CODES) == 16 and {"nib_width_assumed", "proof_png_skipped", "font_stats", "ligature_overlap"} <= INFO_CODES
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

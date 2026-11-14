from font_gen.metrics import (VMetrics, ceil10, collect_vertical, floor10, hmetrics, ligature_carets, lowercase_advances,
                              metric_findings, space_advance, vertical_metrics)
from font_gen.outline import Outline


def _box(x0, y0, x1, y1):
    return Outline.from_recording([("moveTo", ((x0, y0),)), ("lineTo", ((x1, y0),)), ("lineTo", ((x1, y1),)), ("lineTo", ((x0, y1),)), ("closePath", ())])


def test_hmetrics_advance_rounding_lsb_from_outline_and_synthetics(fixture_plan, fixture_outlines, fixture_samples):
    hm = hmetrics(fixture_plan, fixture_outlines, fixture_samples, space_advance=250)
    assert hm[".notdef"] == (500, 50) and hm["space"] == (250, 0)
    for g in fixture_plan.glyphs:
        if g.sample_id is not None:
            adv = round(fixture_samples[g.sample_id].sample.metrics.advance * 1000)
            assert hm[g.name] == (adv, fixture_outlines[g.name].bounds[0])
    assert set(hm) == set(fixture_plan.names())


def test_hmetrics_negative_lsb_and_empty_outline(fixture_plan, fixture_outlines, fixture_samples):
    outlines = dict(fixture_outlines)
    outlines["a"] = _box(-20, 0, 300, 400)
    outlines["a.alt1"] = Outline.empty()
    hm = hmetrics(fixture_plan, outlines, fixture_samples, 250)
    assert hm["a"][1] == -20 and hm["a.alt1"][1] == 0


def test_space_rule_clamp_and_override():
    assert space_advance([0.4, 0.5, 0.6])[0] == 250
    assert space_advance([0.3, 0.3])[0] == 200 and space_advance([0.9, 1.0])[0] == 400
    value, finding = space_advance([0.5])
    assert finding.code == "space_advance_assumed" and finding.severity == "info" and finding.data == {"advance": 250}
    assert space_advance([0.5], override_em=0.33) == (330, None)
    assert space_advance([])[0] == 300


def test_vertical_metrics_floors_and_data_extremes():
    assert ceil10(823) == 830 and floor10(-333) == -340 and ceil10(820) == 820
    vm = vertical_metrics([(0, -100, 500, 650)], [0.45], [450], [700], [700])
    assert vm == VMetrics(ascender=800, descender=-250, line_gap=0, x_height=450, cap_height=700)
    vm2 = vertical_metrics([(0, -333, 500, 823)], [], [440, 460], [], [690])
    assert (vm2.ascender, vm2.descender, vm2.x_height, vm2.cap_height) == (830, -340, 450, 690)
    assert vertical_metrics([], [], [], [], []) == VMetrics(800, -250, 0, 450, 700)


def test_collect_vertical_on_fixture(fixture_plan, fixture_outlines, fixture_samples):
    vm = collect_vertical(fixture_plan, fixture_outlines, fixture_samples)
    assert vm.x_height == 450 and vm.ascender >= 800 and vm.descender <= -250 and vm.line_gap == 0


def test_ligature_carets_proportional_and_even_fallback(fixture_plan):
    hm = {"f": (300, 0), "i": (200, 0), "f_i": (600, 0), "t": (300, 0), "o": (400, 0), "n": (400, 0), "t_i_o_n": (1000, 0),
          "s": (300, 0), "s_t": (500, 0), "f_f": (600, 0), "g": (300, 0), "i_n_g": (900, 0)}
    carets = ligature_carets(fixture_plan, hm)
    assert carets["f_i"] == (360,) and carets["t_i_o_n"] == (231, 385, 692)
    assert carets["f_f"] == (300,) and carets["i_n_g"] == (200, 600)
    del hm["o"]
    assert ligature_carets(fixture_plan, hm)["t_i_o_n"] == (250, 500, 750)


def test_outlier_and_suspicious_flags(fixture_plan, fixture_outlines, fixture_samples):
    assert metric_findings(fixture_plan, fixture_outlines, fixture_samples) == []
    samples = dict(fixture_samples)
    a0 = fixture_plan.by_name()["a"].sample_id
    ls = samples[a0]
    import dataclasses
    wide = ls.sample.model_copy(update={"metrics": ls.sample.metrics.model_copy(update={"advance": 2.0, "x_height": 0.6})})
    samples[a0] = dataclasses.replace(ls, sample=wide)
    codes = {f.code for f in metric_findings(fixture_plan, fixture_outlines, samples) if f.sample_id == a0}
    assert {"advance_outlier", "x_height_outlier"} <= codes
    outlines = dict(fixture_outlines)
    outlines["a"] = _box(0, 0, 800, 400)   # overhang past advance + 0.15 em
    assert any(f.code == "metrics_suspicious" and f.glyph == "a" for f in metric_findings(fixture_plan, outlines, fixture_samples))
    outlines = dict(fixture_outlines)
    outlines["Q"] = _box(0, 0, 400, 300)   # cap height 300 < 550: outside the descriptive table's 0.55-0.90 band
    assert any(f.code == "metrics_suspicious" and f.glyph == "Q" for f in metric_findings(fixture_plan, outlines, fixture_samples))
    outlines["Q"] = Outline.empty()        # empty outlines are the outline checker's business, not this module's
    assert not any(f.glyph == "Q" for f in metric_findings(fixture_plan, outlines, fixture_samples))
    assert len(lowercase_advances(fixture_plan, fixture_samples)) == sum(1 for g in fixture_plan.glyphs if g.sample_id and g.label.islower() and len(g.label) == 1)

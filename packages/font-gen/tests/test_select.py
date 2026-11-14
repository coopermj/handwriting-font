from hwfont_schema import BBox, Context, GlyphStore, Kind, Metrics, PositionInWord, Quality, ReviewStatus, Sample, Target
from hwfont_schema.strokes import Contour, StrokeData, StrokePoint

from font_gen.load import load_store
from font_gen.select import POS_MIN, RING_N, GlyphPlan, default_position, plan_glyphs
from font_gen.synth import synth_store

POS_CTX = {"init": (None, "a"), "medi": ("a", "a"), "fina": ("a", None), "isol": (None, None)}
POS_STORE = {"init": PositionInWord.initial, "medi": PositionInWord.medial, "fina": PositionInWord.final, "isol": PositionInWord.isolated}


def _sample(sid, label, pos="medi", quality=Quality.good, adv=0.5, w=0.4, h=0.4, kind=None):
    left, right = POS_CTX[pos]
    return Sample(
        id=sid, label=label, kind=kind or (Kind.single if len(label) == 1 else Kind.ligature),
        context=Context(source_word="w", left_neighbor=left, right_neighbor=right, position_in_word=POS_STORE[pos]),
        metrics=Metrics(baseline=0.0, x_height=0.45, advance=adv, bbox=BBox(x=0.05, y=0.0, w=w, h=h)),
        quality=quality, review_status=ReviewStatus.accepted, capture_session_id="s", created_at="1970-01-01T00:00:00Z",
    )


def _strokes(w=0.4, h=0.4):
    return StrokeData(contours=[Contour(points=[StrokePoint(x=0.05, y=0.0), StrokePoint(x=0.05 + w, y=h)])])


def _png():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("L", (128, 128), 255).save(buf, format="PNG")
    return buf.getvalue()


def _plan(tmp_path, rows, targets=(), **kw):
    store = GlyphStore.create(tmp_path / "st")
    for t in targets:
        store.add_target(t)
    for sample, strokes, raster in rows:
        store.add_sample(sample, strokes=strokes, raster=raster)
    plan, findings = plan_glyphs(load_store(store), **kw)
    store.close()
    return plan, findings


def test_default_position_rule():
    assert default_position({"medi": 3, "fina": 4}, POS_MIN) == "medi"
    assert default_position({"medi": 2, "fina": 4, "init": 1}, POS_MIN) == "fina"
    assert default_position({"init": 2, "fina": 2}, POS_MIN) == "init"   # tie -> medi, init, fina, isol
    assert default_position({"isol": 1}, POS_MIN) == "isol"


def test_small_positions_merge_into_default_ring_and_pos_min_splits(tmp_path):
    rows = [(_sample(f"m{i}", "a", "medi"), _strokes(), None) for i in range(3)]
    rows += [(_sample(f"f{i}", "a", "fina"), _strokes(), None) for i in range(2)]
    rows += [(_sample("i0", "a", "init"), _strokes(), None)]
    plan, _ = _plan(tmp_path, rows)
    assert plan.rings[("a", None)] == ("a", "a.alt1", "a.alt2", "a.alt3", "a.alt4", "a.alt5")
    assert ("a", "fina") not in plan.rings and plan.positional.get("a", {}) == {}
    plan2, _ = _plan(tmp_path / "2", rows, pos_min=2)
    assert plan2.rings[("a", "fina")] == ("a.fina", "a.fina.alt1") and plan2.positional["a"] == {"fina": "a.fina"}
    assert len(plan2.rings[("a", None)]) == 4


def test_medoid_is_slot_0_and_marginal_never_slot_0(tmp_path):
    rows = [(_sample("s1", "a", adv=0.5, w=0.4, h=0.4), _strokes(), None),
            (_sample("s2", "a", adv=0.52, w=0.42, h=0.41), _strokes(), None),
            (_sample("s3", "a", adv=0.9, w=0.8, h=0.8), _strokes(), None),
            (_sample("s0", "a", quality=Quality.marginal, adv=0.51, w=0.41, h=0.4), _strokes(), None)]
    plan, findings = _plan(tmp_path, rows)
    specs = {g.name: g for g in plan.glyphs}
    assert specs["a"].sample_id == "s2" and specs["a.alt1"].sample_id == "s1" and specs["a.alt2"].sample_id == "s3"
    assert specs["a.alt3"].sample_id == "s0"
    assert not any(f.code == "marginal_used" for f in findings)


def test_truncation_transparent_singleton_and_ring_short(tmp_path):
    rows = [(_sample(f"s{i:02d}", "a"), _strokes(), None) for i in range(9)]
    rows += [(_sample("q1", "Q", "isol"), _strokes(), None)]
    rows += [(_sample("z1", "z"), _strokes(), None), (_sample("z2", "z"), _strokes(), None)]
    plan, findings = _plan(tmp_path, rows)
    assert len(plan.rings[("a", None)]) == RING_N == 7
    surplus = next(f for f in findings if f.code == "ring_surplus")
    assert surplus.label == "a" and len(surplus.data["ids"]) == 2
    assert "Q" in plan.transparent and "space" in plan.transparent and "z" not in plan.transparent
    assert any(f.code == "ring_singleton" and f.glyph == "Q" and f.severity == "warn" for f in findings)
    assert any(f.code == "ring_short" and f.label == "Q" and f.data["n"] == 1 for f in findings)   # R8: both fire for n == 1
    assert any(f.code == "ring_short" and f.label == "z" and f.data["n"] == 2 for f in findings)
    assert not any(f.code == "ring_short" and f.label == "a" for f in findings)


def test_punctuation_and_digits_are_never_positional(tmp_path):
    rows = [(_sample(f"p{i}", ".", "medi"), _strokes(), None) for i in range(3)]
    rows += [(_sample(f"f{i}", ".", "fina"), _strokes(), None) for i in range(3)]
    rows += [(_sample(f"d{i}", "7", "init"), _strokes(), None) for i in range(3)]
    plan, _ = _plan(tmp_path, rows)
    assert len(plan.rings[(".", None)]) == 6 and (".", "fina") not in plan.rings
    assert plan.rings[("7", None)] == ("seven", "seven.alt1", "seven.alt2")
    assert "period" not in plan.letters and "seven" not in plan.letters


def test_every_eligible_sample_maps_to_exactly_one_glyph(fixture_store):
    loaded = load_store(fixture_store)
    plan, _ = plan_glyphs(loaded)
    mapped = [g.sample_id for g in plan.glyphs if g.sample_id is not None]
    assert len(mapped) == len(set(mapped)) == len(loaded.samples)
    assert plan.glyphs[0].name == ".notdef" and plan.glyphs[1].name == "space"
    names = plan.names()
    assert names[:12] == [".notdef", "space", "Q", "a", "a.alt1", "a.alt2", "a.alt3", "a.alt4", "a.alt5",
                          "a.fina", "a.fina.alt1", "a.fina.alt2"]
    assert names.index("f_i") > names.index("t") and names.index("f_f") < names.index("f_i") < names.index("i_n_g")


def test_ligature_allowed_positions_components_and_restriction(fixture_store):
    plan, findings = plan_glyphs(load_store(fixture_store))
    assert plan.ligatures["t_i_o_n"] == (("t", "i", "o", "n"), frozenset({"fina"}))
    assert plan.ligatures["f_i"] == (("f", "i"), frozenset({"medi"}))
    restricted = {f.label for f in findings if f.code == "ligature_restricted"}
    assert restricted == {"fi", "ing", "tion", "ff", "st"}
    assert "f_i" in plan.letters and "f_i.alt3" in plan.letters and "a.fina.alt2" in plan.letters


def test_missing_component_emits_glyph_without_rule(tmp_path):
    rows = [(_sample(f"l{i}", "fi"), _strokes(), None) for i in range(2)]
    rows += [(_sample("i1", "i"), _strokes(), None)]
    plan, findings = _plan(tmp_path, rows)
    assert "f_i" in plan.names() and "f_i" not in plan.ligatures
    assert any(f.code == "ligature_component_missing" and f.label == "fi" and f.severity == "warn" for f in findings)


def test_targets_no_samples_below_required_bad_and_marginal(e2e_store):
    plan, findings = plan_glyphs(load_store(e2e_store))
    assert "k" not in plan.names() and ord("k") not in plan.cmap
    assert any(f.code == "target_no_samples" and f.label == "k" and f.severity == "warn" for f in findings)
    below = next(f for f in findings if f.code == "target_below_required" and f.label == "b")
    assert below.data == {"required": 12, "accepted": 12, "eligible": 2} and plan.eligible["b"] == 2
    assert sum(f.code == "bad_sample_excluded" for f in findings) == 10
    assert any(f.code == "marginal_used" and f.label == "z" for f in findings)


def test_marginal_strokes_outrank_a_raster_only_good(tmp_path):
    # spec 7.6: a raster-fallback glyph is never slot 0 when a stroke-sourced sample exists (Review Focus 1)
    rows = [(_sample("r1", "a"), None, _png()), (_sample("m1", "a", quality=Quality.marginal), _strokes(), None)]
    plan, findings = _plan(tmp_path, rows)
    specs = {g.name: g for g in plan.glyphs}
    assert specs["a"].sample_id == "m1" and specs["a"].source == "strokes"
    assert specs["a.alt1"].sample_id == "r1" and specs["a.alt1"].source == "raster"
    assert any(f.code == "marginal_used" and f.label == "a" for f in findings)


def test_cmap_is_base_only_with_aliases(fixture_store):
    plan, _ = plan_glyphs(load_store(fixture_store))
    assert plan.cmap[ord("a")] == "a" and plan.cmap[0x20] == "space" and plan.cmap[0xA0] == "space"
    assert 0x2019 not in plan.cmap   # quotesingle not in this store
    assert all("." not in n[1:] and "_" not in n for n in plan.cmap.values())


def test_empty_store_is_an_error_and_ring_not_prime(tmp_path):
    plan, findings = _plan(tmp_path, [(_sample("b1", "a", quality=Quality.bad), _strokes(), None)])
    assert any(f.code == "empty_font" and f.severity == "error" for f in findings)
    assert plan.names() == [".notdef", "space"]
    _, f6 = _plan(tmp_path / "6", [(_sample("a1", "a"), _strokes(), None)], ring_n=6)
    assert any(f.code == "ring_not_prime" for f in f6)


def test_slots_after_the_medoid_are_goods_then_marginals_by_id(tmp_path):
    # ruling R7: slot 0 is the medoid of the first non-empty group; slots >= 1 follow spec 6.5 only (all remaining
    # goods by id, stroke and raster together, then all marginals by id): [g1, r1, m1], not [g1, m1, r1]
    rows = [(_sample("g1", "a"), _strokes(), None), (_sample("m1", "a", quality=Quality.marginal), _strokes(), None),
            (_sample("r1", "a"), None, _png())]
    plan, _ = _plan(tmp_path, rows)
    specs = {g.name: g for g in plan.glyphs}
    assert [specs[n].sample_id for n in plan.rings[("a", None)]] == ["g1", "r1", "m1"]
    rows = [(_sample("g2", "a"), _strokes(), None), (_sample("g1", "a"), _strokes(), None),
            (_sample("m0", "a", quality=Quality.marginal), _strokes(), None), (_sample("r0", "a"), None, _png()),
            (_sample("r9", "a", quality=Quality.marginal), None, _png())]
    plan, _ = _plan(tmp_path / "2", rows)
    specs = {g.name: g for g in plan.glyphs}
    assert [specs[n].sample_id for n in plan.rings[("a", None)]] == ["g1", "g2", "r0", "m0", "r9"]

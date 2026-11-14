import pytest
from hwfont_schema import Kind, Quality, ReviewStatus, check_sample_em
from hwfont_schema.emspace import png_size

from font_gen.synth import default_targets, demo_spec, synth_store


def test_default_targets_match_capture_template_shape():
    targets = default_targets()
    assert len(targets) == 121
    assert sum(len(t.label) == 1 for t in targets) == 73 and sum(len(t.label) > 1 for t in targets) == 48
    by_label = {t.label: t for t in targets}
    assert by_label["a"].required_count == 12 and by_label["fi"].required_count == 8
    assert by_label["Sch"].required_count == 3 and by_label["str"].required_count == 4
    assert by_label["Th"].kind is Kind.ligature and by_label["("].kind is Kind.single


def test_default_targets_do_not_drift_from_capture_template():
    ct = pytest.importorskip("capture_template.targets")   # the copy is checked against the original when it is installed
    ours = {(t.label, t.kind, t.required_count) for t in default_targets()}
    theirs = {(t.label, t.kind, t.required_count) for t in ct.default_targets()}
    assert ours == theirs


def test_demo_spec_covers_every_default_target(tmp_path):
    spec = demo_spec()
    assert set(spec) == {t.label for t in default_targets()}
    store = synth_store(tmp_path / "demo", spec, seed=1, rasters=False)
    labels = {s.label for s in store.all_samples(ReviewStatus.accepted)}
    store.close()
    assert labels == set(spec)


def test_fixture_samples_are_em_v1_clean_with_and_without_rasters(fixture_store):
    for s in fixture_store.all_samples():
        strokes = fixture_store.load_strokes(s)
        raster = fixture_store.load_raster(s)
        assert raster is not None and strokes is not None
        assert check_sample_em(s, strokes, raster) == [], s.id
        assert check_sample_em(s, strokes, None) == []
        w, h = png_size(raster)
        assert w >= 32 and h >= 32


def test_flags_shape_the_store(e2e_store):
    q = e2e_store.samples_for("q")
    assert all(s.strokes_path is None and s.raster_path is not None for s in q)
    b = e2e_store.samples_for("b")
    assert sum(s.quality is Quality.bad for s in b) == 10 and len(b) == 12
    assert [s.quality for s in e2e_store.samples_for("z")].count(Quality.marginal) == 1
    assert all(s.kind is Kind.ligature for s in e2e_store.samples_for("e"))
    assert "k" in {t.label for t in e2e_store.targets()} and e2e_store.samples_for("k") == []
    xs = e2e_store.samples_for("x")[0]
    x = e2e_store.load_strokes(xs)
    assert len(x.contours) == 1 and x.contours[0].points[0] == x.contours[0].points[1]
    assert xs.metrics.advance == 0.5 and xs.metrics.bbox.w == 0.001


def test_seed_reproducible_and_variants_differ(tmp_path):
    spec = {"a": {"medi": 2}}
    s1 = synth_store(tmp_path / "s1", spec, seed=7)
    s2 = synth_store(tmp_path / "s2", spec, seed=7)
    s3 = synth_store(tmp_path / "s3", spec, seed=8)
    a1, a2, a3 = (st.samples_for("a") for st in (s1, s2, s3))
    assert [s1.load_strokes(s) for s in a1] == [s2.load_strokes(s) for s in a2]
    assert s1.load_strokes(a1[0]) != s3.load_strokes(a3[0])
    assert s1.load_strokes(a1[0]) != s1.load_strokes(a1[1])  # jitter makes variants differ
    assert s1.load_raster(a1[0]) == s2.load_raster(a2[0])
    for st in (s1, s2, s3):
        st.close()


def test_pressure_and_width_options(tmp_path):
    st = synth_store(tmp_path / "p", {"a": {"medi": 1}, "o": {"medi": 1}}, pressure=True, widths=frozenset({"o"}))
    a = st.load_strokes(st.samples_for("a")[0])
    o = st.load_strokes(st.samples_for("o")[0])
    st.close()
    assert all(p.pressure is not None for c in a.contours for p in c.points)
    assert all(p.width is None for c in a.contours for p in c.points)
    assert all(p.width is not None and 0 < p.width <= 0.2 for c in o.contours for p in c.points)

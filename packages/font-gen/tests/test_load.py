from hwfont_schema import BBox, Context, GlyphStore, Kind, Metrics, PositionInWord, Quality, ReviewStatus, Sample, Target
from hwfont_schema.strokes import Contour, StrokeData, StrokePoint

from conftest import FIXTURE_SPEC
from font_gen.load import LoadedSample, is_letterish, load_store, shaping_position, store_digest
from font_gen.synth import synth_store


def _ctx(left, right, pos=PositionInWord.medial, word="word"):
    return Context(source_word=word, left_neighbor=left, right_neighbor=right, position_in_word=pos)


def test_shaping_position_from_neighbours():
    assert shaping_position(_ctx("f", "o")) == "medi"
    assert shaping_position(_ctx("o", ".", PositionInWord.medial, "fox.")) == "fina"   # fox. -> final x
    assert shaping_position(_ctx("n", "'", PositionInWord.medial, "don't")) == "medi"  # apostrophe is letterish
    assert shaping_position_from(None, "a") == "init"
    assert shaping_position_from(None, None) == "isol"
    assert shaping_position_from("a", None) == "fina"
    assert shaping_position_from(" ", "a") == "init"


def shaping_position_from(left, right):
    return shaping_position(_ctx(left, right))


def test_is_letterish():
    assert is_letterish("a") and is_letterish("Z") and is_letterish("'")
    assert not is_letterish(None) and not is_letterish(".") and not is_letterish(" ") and not is_letterish("-")


def _sample(sid, label, kind=Kind.single, ctx=None, strokes_ok=True):
    return Sample(
        id=sid, label=label, kind=kind, context=ctx or _ctx("a", "a"),
        metrics=Metrics(baseline=0.0, x_height=0.45, advance=0.6, bbox=BBox(x=0.05, y=0.0, w=0.5, h=0.5)),
        quality=Quality.good, review_status=ReviewStatus.accepted, capture_session_id="s", created_at="1970-01-01T00:00:00Z",
    )


def _strokes():
    return StrokeData(contours=[Contour(points=[StrokePoint(x=0.05, y=0.0), StrokePoint(x=0.55, y=0.5)])])


def test_load_flags_position_rederived_sidecar_missing_no_ink_and_kind_mismatch(tmp_path):
    store = GlyphStore.create(tmp_path / "st")
    store.add_target(Target(label="x", kind=Kind.single, required_count=1))
    store.add_target(Target(label="fi", kind=Kind.ligature, required_count=1))
    store.add_sample(_sample("s1", "x", ctx=_ctx("o", ".", PositionInWord.medial, "fox.")), strokes=_strokes())
    gone = store.add_sample(_sample("s2", "x"), strokes=_strokes())
    (tmp_path / "st" / "strokes" / "s2.json").unlink()
    store.add_sample(_sample("s3", "x"))  # neither strokes nor raster
    store.add_sample(_sample("s4", "fi", kind=Kind.single), strokes=_strokes())      # 2-char label as single
    store.add_sample(_sample("s5", "x", kind=Kind.ligature), strokes=_strokes())     # disagrees with its Target
    store.add_sample(_sample("s6", "x").model_copy(update={"review_status": ReviewStatus.pending}), strokes=_strokes())
    result = load_store(store)
    store.close()

    ids = [ls.sample.id for ls in result.samples]
    assert ids == ["s1", "s4", "s5"]            # s2/s3 excluded, s6 never read
    codes = {(f.code, f.sample_id) for f in result.findings}
    assert ("position_rederived", "s1") in codes and result.samples[0].position == "fina"
    assert ("sidecar_missing", "s2") in codes
    assert ("sample_no_ink", "s3") in codes
    assert ("kind_mismatch", "s4") in codes and ("kind_mismatch", "s5") in codes
    assert "kind_mismatch" in result.samples[1].flags
    assert len(result.targets) == 2 and {r.label for r in result.coverage} == {"x", "fi"}


def test_convention_violation_and_raster_scale_mismatch(tmp_path):
    store = GlyphStore.create(tmp_path / "st")
    bad_strokes = StrokeData(contours=[Contour(points=[StrokePoint(x=0.05, y=0.0), StrokePoint(x=0.55, y=-0.5)])])
    store.add_sample(_sample("s1", "x"), strokes=bad_strokes)
    png = synth_sample_png(154, 160)
    store.add_sample(_sample("s2", "x"), raster=png)          # raster-only, scales disagree -> excluded
    store.add_sample(_sample("s3", "x"), strokes=_strokes(), raster=png)  # strokes keep it; raster dropped
    result = load_store(store)
    store.close()
    by_id = {ls.sample.id: ls for ls in result.samples}
    assert "s1" in by_id and any(f.code == "convention_violation" and f.sample_id == "s1" for f in result.findings)
    assert "s2" not in by_id
    assert by_id["s3"].raster is None and by_id["s3"].strokes is not None
    assert sum(f.code == "raster_scale_mismatch" for f in result.findings) == 2
    # ruling R6 (spec 5.3 "additionally"): the violation string is a convention_violation as well
    mismatch_cv = sorted(f.sample_id for f in result.findings if f.code == "convention_violation"
                         and f.data["violation"].startswith("raster_scale_mismatch"))
    assert mismatch_cv == ["s2", "s3"]
    assert sum(f.code == "sample_no_ink" for f in result.findings) == 0


def synth_sample_png(w, h):
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("L", (w, h), 255).save(buf, format="PNG")
    return buf.getvalue()


def test_digest_is_stable_and_store_sensitive(tmp_path):
    a = synth_store(tmp_path / "a", {"a": {"medi": 2}}, seed=1)
    b = synth_store(tmp_path / "b", {"a": {"medi": 2}}, seed=1)
    c = synth_store(tmp_path / "c", {"a": {"medi": 3}}, seed=1)
    da, db, dc = store_digest(a), store_digest(b), store_digest(c)
    for st in (a, b, c):
        st.close()
    assert da == db and len(da) == 64 and da != dc


def test_load_fixture_store_is_clean(fixture_store):
    result = load_store(fixture_store)
    assert all(isinstance(ls, LoadedSample) for ls in result.samples)
    assert len(result.samples) == sum(sum(v.values()) for v in FIXTURE_SPEC.values())
    assert {f.code for f in result.findings} == set()

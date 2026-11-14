"""End to end: a synthetic CandidateSet reviewed headlessly yields a store font-gen builds with zero QA errors."""

from __future__ import annotations

from conftest import NOW
from font_gen.load import store_digest
from font_gen.qa import shape_names
from font_gen.run import BuildOptions, run_build
from hwfont_schema import GlyphStore, Quality
from hwfont_schema.emspace import check_sample_em

from review.coverage import report
from review.decisions import accept, default_log, load_log
from review.inputs import load_inputs
from review.session import compute_stats
from review.store_sync import apply, replay, seed_targets
from synth_page import synth_page

TRANSCRIPTS = ["the cat sat on the mat", "a fat hen ate the nut", "that hat is the hat", "ten tan cats eat", "the the the"]


def test_candidates_through_review_to_font(tmp_path):
    sp = synth_page(
        tmp_path,
        TRANSCRIPTS,
        seed=7,
        ligatures={2: [(0, "th"), (9, "th")], 4: [(0, "th"), (3, "th"), (6, "th")]},
    )
    store = GlyphStore.create(tmp_path / "store")
    inputs = load_inputs(sp.sidecar_path, [sp.candidate_dir], {"p0": sp.raster_path})
    stats = compute_stats(inputs)
    seed_targets(store, inputs)
    log = default_log(inputs, NOW)
    for cid in list(log.decisions):
        log, d = apply(store, inputs, stats, log, sp.candidate_dir, lambda l, c=cid: accept(l, c, NOW), cid)
        assert d.quality == Quality.good and d.flags == [], (cid, d.flags)
    for s in store.all_samples():
        assert check_sample_em(s, store.load_strokes(s), store.load_raster(s)) == []
    assert all(line.accepted >= 2 for line in report(store, [log]).lines if line.label in "theat")

    result = run_build(store, BuildOptions())
    assert result.otf is not None and result.report.has_errors is False, result.report.findings
    bad = [f for f in result.report.findings if f.code in ("convention_violation", "kind_mismatch", "sample_no_ink")]
    assert not bad, bad
    names = shape_names(result.otf, "the hat")
    assert ".notdef" not in names and len(names) >= 6, names
    that = shape_names(result.otf, "that")
    assert any(n.startswith("t_h") for n in that), that  # the th ligature fired

    replayed = GlyphStore.create(tmp_path / "replayed")
    seed_targets(replayed, inputs)
    replay(replayed, inputs, stats, load_log(sp.candidate_dir, inputs, NOW))
    assert run_build(replayed, BuildOptions()).otf == result.otf
    assert store_digest(replayed) == store_digest(store)

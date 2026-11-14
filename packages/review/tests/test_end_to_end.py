"""End to end: a synthetic CandidateSet reviewed headlessly yields a store font-gen builds with zero QA errors."""

from __future__ import annotations

import pytest
from conftest import NOW
from font_gen.load import store_digest
from font_gen.qa import shape_names
from font_gen.run import BuildOptions, run_build
from hwfont_schema import GlyphStore, Quality
from hwfont_schema.emspace import check_sample_em

from review.coverage import report
from review.decisions import accept, default_log, load_log
from review.inputs import load_inputs
from review.normalize import sample_id
from review.session import compute_stats, unit_order
from review.store_sync import apply, replay, seed_targets
from synth_page import synth_page

TRANSCRIPTS = ["the cat sat on the mat", "a fat hen ate the nut", "that hat is the hat", "ten tan cats eat", "the the the"]


@pytest.mark.parametrize("contexts", ["transcript", "ingest"])
def test_candidates_through_review_to_font(tmp_path, contexts):
    """`contexts="ingest"` gives every box after a ligature box today's shifted ingest Context (C1)."""
    sp = synth_page(
        tmp_path,
        TRANSCRIPTS,
        seed=7,
        ligatures={2: [(0, "th"), (9, "th")], 4: [(0, "th"), (3, "th"), (6, "th")]},
        contexts=contexts,
    )
    store = GlyphStore.create(tmp_path / "store")
    inputs = load_inputs(sp.sidecar_path, [sp.candidate_dir], {"p0": sp.raster_path})
    stats = compute_stats(inputs)
    seed_targets(store, inputs)
    log = default_log(inputs, NOW)
    for cid in list(log.decisions):
        log, d = apply(store, inputs, stats, log, sp.candidate_dir, lambda l, c=cid: accept(l, c, NOW), cid)
        assert d.quality == Quality.good and d.flags == [], (cid, d.flags)
    pg = inputs.pages["p0"]
    r2 = unit_order(pg, sp.page.regions[2].id)  # "that hat is the hat": th a t h a t i s th e h a t
    e_of_the, h_of_hat = store.get_sample(sample_id(inputs.session_id, r2[9])), store.get_sample(
        sample_id(inputs.session_id, r2[10]))
    assert (e_of_the.label, e_of_the.context.source_word, e_of_the.context.position_in_word.value) == (
        "e", "the", "final")
    assert e_of_the.context.left_neighbor == "h" and e_of_the.context.right_neighbor is None
    assert (h_of_hat.label, h_of_hat.context.source_word, h_of_hat.context.position_in_word.value) == (
        "h", "hat", "initial")
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

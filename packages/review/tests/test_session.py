from __future__ import annotations

import dataclasses

import pytest
from synth_page import synth_page

from review.ink import candidate_extent
from review.inputs import load_inputs
from review.session import (
    HALF_GAP_DEFAULT,
    X_HEIGHT_DEFAULT,
    SessionStats,
    compute_stats,
    context_mismatch,
    effective_context,
    neighbours,
    unit_order,
)


def test_unit_order_follows_original_boxes_and_ties_by_id(inputs, synth):  # Review Focus 2
    pg = inputs.pages["p0"]
    r0 = synth.page.regions[0].id
    ids = unit_order(pg, r0)
    assert ids == sorted(pg.candidate_ids(r0), key=lambda i: pg.candidates[i].bbox.x)
    dup = pg.candidates[ids[1]].model_copy(update={"bbox": pg.candidates[ids[0]].bbox})  # duplicate box
    pg2 = dataclasses.replace(pg, candidates={**pg.candidates, ids[1]: dup})
    assert unit_order(pg2, r0)[:2] == sorted(ids[:2])
    assert compute_stats(dataclasses.replace(inputs, pages={"p0": pg2})).n_gaps >= 1  # a zero gap is not an error


def test_neighbours_only_where_context_says_and_labels_agree(inputs, synth):
    pg = inputs.pages["p0"]
    r0 = synth.page.regions[0].id  # "the cat sat"
    ids = unit_order(pg, r0)
    assert neighbours(pg, r0, ids[0]) == (None, ids[1])  # 't' of "the"
    assert neighbours(pg, r0, ids[2]) == (ids[1], None)  # 'e' of "the": word edge, 'c' is not a neighbour
    assert neighbours(pg, r0, ids[4]) == (ids[3], ids[5])  # 'a' of "cat"
    shifted = pg.candidates[ids[3]].model_copy(update={"label": "q"})  # vision mislabelled 'c'
    pg2 = dataclasses.replace(pg, candidates={**pg.candidates, ids[3]: shifted})
    assert neighbours(pg2, r0, ids[4]) == (None, ids[5]) and context_mismatch(pg2, r0, ids[4]) == (True, False)
    r2 = synth.page.regions[2].id  # "the hat" with a 'th' ligature box
    th, e = unit_order(pg, r2)[:2]
    assert pg.candidates[th].label == "th" and neighbours(pg, r2, e) == (th, None)  # 'e' sees the ligature's last char


def test_x_height_is_median_of_p90_tops(inputs):
    stats = compute_stats(inputs)
    assert stats.n_x_height_letters >= 6 and stats.flags == ()
    assert 0.38 <= stats.x_height <= 0.46  # loops top out at exactly 0.45 em
    assert 0.0 < stats.median_half_gap < 0.15 and stats.n_gaps >= 6


def test_stats_defaults_when_no_data(tmp_path):
    sp = synth_page(tmp_path, ["T"])  # no x-height letter, no neighbour
    stats = compute_stats(load_inputs(sp.sidecar_path, [sp.candidate_dir]))
    assert stats == SessionStats(X_HEIGHT_DEFAULT, HALF_GAP_DEFAULT, 0, 0, ("x_height_assumed", "half_gap_assumed"))


def test_half_gap_hand_checked(tmp_path):
    sp = synth_page(tmp_path, ["ll"])
    inputs = load_inputs(sp.sidecar_path, [sp.candidate_dir])
    pg = inputs.pages["p0"]
    a, b = unit_order(pg, sp.page.regions[0].id)
    ea, eb = (candidate_extent(pg.candidates[i], pg.strokes.get(i), pg.crops.get(i)) for i in (a, b))
    assert compute_stats(inputs).median_half_gap == pytest.approx(0.5 * (eb.xmin - ea.xmax) / 70)


def test_effective_context_undoes_ingest_shift_after_ligature(tmp_path):  # C1
    sp = synth_page(tmp_path, ["the cat sat", "the hat"], ligatures={1: [(0, "th")]}, contexts="ingest")
    inputs = load_inputs(sp.sidecar_path, [sp.candidate_dir])
    pg = inputs.pages["p0"]
    r1 = sp.page.regions[1].id
    th, e, h, a, t = unit_order(pg, r1)
    assert pg.candidates[e].context.position_in_word.value == "medial"  # today's ingest: shifted by one
    assert pg.candidates[h].context.source_word == "the"
    ctx_e, ctx_h = effective_context(pg, r1, e), effective_context(pg, r1, h)
    assert (ctx_e.source_word, ctx_e.position_in_word.value, ctx_e.left_neighbor, ctx_e.right_neighbor) == (
        "the", "final", "h", None)
    assert (ctx_h.source_word, ctx_h.position_in_word.value, ctx_h.left_neighbor, ctx_h.right_neighbor) == (
        "hat", "initial", None, "a")
    ctx_th = effective_context(pg, r1, th)
    assert (ctx_th.position_in_word.value, ctx_th.right_neighbor) == ("initial", "e")
    assert neighbours(pg, r1, e) == (th, None) and neighbours(pg, r1, h) == (None, a)
    for cid in unit_order(pg, r1):
        assert context_mismatch(pg, r1, cid) == (False, False), cid
    truth = synth_page(tmp_path / "t", ["the cat sat", "the hat"], ligatures={1: [(0, "th")]})
    assert compute_stats(inputs) == compute_stats(load_inputs(truth.sidecar_path, [truth.candidate_dir]))


def test_effective_context_falls_back_when_labels_do_not_spell_transcript(tmp_path):  # C1
    sp = synth_page(tmp_path, ["the hat"], ligatures={0: [(0, "th")]}, contexts="ingest")
    inputs = load_inputs(sp.sidecar_path, [sp.candidate_dir])
    pg = inputs.pages["p0"]
    r0 = sp.page.regions[0].id
    ids = unit_order(pg, r0)
    wrong = pg.candidates[ids[2]].model_copy(update={"label": "q"})  # vision misread 'h' of "hat"
    pg2 = dataclasses.replace(pg, candidates={**pg.candidates, ids[2]: wrong})
    for cid in ids:
        assert effective_context(pg2, r0, cid) == pg2.candidates[cid].context

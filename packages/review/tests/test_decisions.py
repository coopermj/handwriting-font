from __future__ import annotations

import pytest
from hwfont_schema import BBox, Kind, Quality

from conftest import NOW
from review.decisions import (
    DecisionStatus,
    accept,
    adjust_box,
    default_decision,
    default_log,
    load_log,
    reject,
    relabel,
    reset,
    save_log,
)
from review.errors import ReviewError


def test_default_log_mirrors_candidates(inputs):
    log = default_log(inputs, NOW)
    c = inputs.candidate(next(iter(log.decisions)))
    d = log.decisions[c.id]
    assert d.status == DecisionStatus.pending and d.label == c.label and d.kind == c.kind and d.bbox == c.bbox
    assert d.quality == Quality.good and d.flags == [] and d.updated_at == NOW


def test_ops_are_pure_and_set_status(inputs):
    log = default_log(inputs, NOW); cid = next(iter(log.decisions))
    acc = accept(log, cid, "t1", Quality.marginal)
    assert log.decisions[cid].status == DecisionStatus.pending
    assert acc.decisions[cid].status == DecisionStatus.accepted and acc.decisions[cid].quality == Quality.marginal
    assert acc.decisions[cid].updated_at == "t1"
    rej = reject(acc, cid, "t2"); assert rej.decisions[cid].status == DecisionStatus.rejected
    with pytest.raises(ReviewError): accept(log, "nope", NOW)


def test_relabel_follows_kind_rule_and_flags_unexpected(inputs):
    log = default_log(inputs, NOW); cid = next(iter(log.decisions))
    d = relabel(log, inputs, cid, "th", NOW).decisions[cid]
    assert d.kind == Kind.ligature
    d2 = relabel(log, inputs, cid, "Q", NOW).decisions[cid]
    assert d2.kind == Kind.single and "label_not_expected" in d2.flags
    assert "label_not_expected" not in relabel(log, inputs, cid, "t", NOW).decisions[cid].flags
    for bad in ("", "a b"):
        with pytest.raises(ReviewError): relabel(log, inputs, cid, bad, NOW)


def test_adjust_box_must_stay_on_page_and_touch_region(inputs):
    log = default_log(inputs, NOW); cid = next(iter(log.decisions))
    page = inputs.page_of(cid).page; region = inputs.page_of(cid).region(inputs.candidate(cid).region_id)
    inside = BBox(x=region.bbox.x + 1, y=region.bbox.y + 1, w=20, h=30)
    assert adjust_box(log, inputs, cid, inside, NOW).decisions[cid].bbox == inside
    overhang = BBox(x=region.bbox.x + 1, y=region.bbox.y + region.bbox.h - 10, w=20, h=30)   # dips below the row
    assert adjust_box(log, inputs, cid, overhang, NOW).decisions[cid].bbox == overhang
    assert adjust_box(log, inputs, cid, inputs.candidate(cid).bbox, NOW).decisions[cid].bbox == inputs.candidate(cid).bbox  # the original always re-submits
    for bad in (BBox(x=-1, y=region.bbox.y, w=20, h=20),                                   # off the page
                BBox(x=region.bbox.x, y=region.bbox.y + region.bbox.h + 5, w=20, h=20),  # misses the region
                BBox(x=page.width_px - 10, y=region.bbox.y, w=20, h=20)):                 # past the right edge
        with pytest.raises(ReviewError):
            adjust_box(log, inputs, cid, bad, NOW)


def test_reset_restores_original(inputs):
    log = default_log(inputs, NOW); cid = next(iter(log.decisions))
    changed = accept(relabel(log, inputs, cid, "Q", NOW), cid, NOW)
    assert reset(changed, inputs, cid, "t9").decisions[cid] == default_decision(inputs.candidate(cid), "t9")


def test_log_round_trip_growth_and_unknown_id(inputs, synth):
    log = default_log(inputs, NOW); cid = next(iter(log.decisions))
    save_log(synth.candidate_dir, accept(log, cid, NOW))
    assert not list(synth.candidate_dir.glob("*.tmp"))
    again = load_log(synth.candidate_dir, inputs, "later")
    assert again.decisions[cid].status == DecisionStatus.accepted
    shrunk = again.model_copy(update={"decisions": {cid: again.decisions[cid]}})
    save_log(synth.candidate_dir, shrunk)
    grown = load_log(synth.candidate_dir, inputs, "later")
    assert set(grown.decisions) == set(log.decisions) and grown.decisions[cid].status == DecisionStatus.accepted
    alien = again.model_copy(update={"decisions": {**again.decisions, "zz": again.decisions[cid].model_copy(update={"candidate_id": "zz"})}})
    save_log(synth.candidate_dir, alien)
    with pytest.raises(ReviewError, match="zz"): load_log(synth.candidate_dir, inputs, "later")
    save_log(synth.candidate_dir, again.model_copy(update={"session_id": "other"}))
    with pytest.raises(ReviewError, match="other"): load_log(synth.candidate_dir, inputs, "later")


def test_load_defaults_when_absent_and_unknown_dir(inputs, synth, tmp_path):
    assert set(load_log(synth.candidate_dir, inputs, NOW).decisions) == set(default_log(inputs, NOW).decisions)
    with pytest.raises(ReviewError):
        load_log(tmp_path / "elsewhere", inputs, NOW)


def test_empty_vision_label(inputs):  # I3
    import dataclasses

    from review.decisions import default_log
    from review.session import compute_stats, context_mismatch, neighbours, unit_order

    pg = inputs.pages["p0"]; r0 = pg.page.regions[0].id; ids = unit_order(pg, r0)
    blank = pg.candidates[ids[1]].model_copy(update={"label": ""})
    inputs2 = dataclasses.replace(inputs, pages={"p0": dataclasses.replace(pg, candidates={**pg.candidates, ids[1]: blank})})
    pg2 = inputs2.pages["p0"]
    assert neighbours(pg2, r0, ids[0]) == (None, None) and context_mismatch(pg2, r0, ids[0]) == (False, True)
    assert neighbours(pg2, r0, ids[2]) == (None, None)
    compute_stats(inputs2)  # no crash
    log = default_log(inputs2, NOW)
    with pytest.raises(ReviewError, match="empty label; relabel first"):
        accept(log, ids[1], NOW)
    assert accept(relabel(log, inputs2, ids[1], "h", NOW), ids[1], NOW).decisions[ids[1]].status.value == "accepted"


def test_default_decision_kind_follows_label_length(inputs):  # minor
    from hwfont_schema import Kind

    c = next(iter(inputs.pages["p0"].candidates.values()))
    lig_by_vision = c.model_copy(update={"label": "e", "kind": Kind.ligature})
    assert default_decision(lig_by_vision, NOW).kind == Kind.single
    single_by_vision = c.model_copy(update={"label": "th", "kind": Kind.single})
    assert default_decision(single_by_vision, NOW).kind == Kind.ligature

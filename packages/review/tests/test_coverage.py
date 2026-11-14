from __future__ import annotations

from conftest import NOW, _id_of
from hwfont_schema import Kind, Target

from review.coverage import CoverageLine, CoverageReport, format_report, report
from review.decisions import DecisionStatus, relabel
from review.store_sync import apply, seed_targets


def _scenario(store, inputs, stats, log, synth):
    seed_targets(store, inputs)
    from review.decisions import accept

    for region in (0, 1):
        cid = _id_of(inputs, "a", region)
        log, _ = apply(store, inputs, stats, log, synth.candidate_dir, lambda l, c=cid: accept(l, c, NOW), cid)
    qid = _id_of(inputs, "h", 0)
    log, _ = apply(store, inputs, stats, log, synth.candidate_dir, lambda l: relabel(l, inputs, qid, "Q", NOW), qid)
    return log


def test_report_counts_and_order(store, inputs, stats, log, synth):
    log = _scenario(store, inputs, stats, log, synth)
    rep = report(store, [log])
    assert isinstance(rep, CoverageReport) and all(isinstance(l, CoverageLine) for l in rep.lines)
    a = next(l for l in rep.lines if l.label == "a")
    pending_a = sum(
        1 for d in log.decisions.values() if d.label == "a" and d.status == DecisionStatus.pending
    )
    assert a.accepted == 2 and a.pending == pending_a >= 1 and a.met is False
    assert rep.total == len(rep.lines) and rep.met_count == sum(l.met for l in rep.lines)
    unmet = [l for l in rep.lines if not l.met]
    assert list(rep.lines[: len(unmet)]) == sorted(unmet, key=lambda l: l.label)
    assert ("Q", Kind.single, 1) in rep.untargeted
    text = format_report(rep)
    assert text.splitlines()[0] == f"coverage: {rep.met_count}/{rep.total} targets met"
    assert f"a: 2/12 (+{pending_a} pending)" in text
    assert "✓" not in text and "untargeted: Q×1" in text and not text.endswith("\n")


def test_met_line_and_order(store, inputs, stats, log, synth):
    log = _scenario(store, inputs, stats, log, synth)
    store.add_target(Target(label="a", kind=Kind.single, required_count=2))
    rep = report(store, [log])
    a = next(l for l in rep.lines if l.label == "a")
    assert a.met and rep.lines[-1] == a and rep.met_count == 1
    text = format_report(rep)
    assert f"a: 2/2 ✓ (+{a.pending} pending)" in text
    assert sum("✓" in ln for ln in text.splitlines()) == 1


def test_untargeted_keyed_on_label_and_kind(store, inputs, stats, log, synth):  # I8
    from review.decisions import reject

    store.add_target(Target(label="th", kind=Kind.single, required_count=1))  # kind disagrees with the label
    store.add_target(Target(label="a", kind=Kind.single, required_count=1))
    th = _id_of(inputs, "th", 2)  # a ligature decision for "th"
    rep = report(store, [log])
    assert ("th", Kind.ligature, 1) in rep.untargeted  # kind mismatch surfaces
    assert "th (ligature)×1" in format_report(rep)
    q = _id_of(inputs, "c", 0)
    log2, _ = apply(store, inputs, stats, log, synth.candidate_dir, lambda l: relabel(l, inputs, q, "Q", NOW), q)
    assert ("Q", Kind.single, 1) in report(store, [log2]).untargeted
    log3, _ = apply(store, inputs, stats, log2, synth.candidate_dir, lambda l: reject(l, q, NOW), q)
    assert all(lab != "Q" for lab, _, _ in report(store, [log3]).untargeted)  # rejected excluded
    two = report(store, [log2, log2]).untargeted  # two logs sum
    assert ("Q", Kind.single, 2) in two and ("th", Kind.ligature, 2) in two
    assert log.decisions[th].kind == Kind.ligature

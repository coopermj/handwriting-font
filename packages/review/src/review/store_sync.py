"""Write-through store synchronisation: the only module that writes to the GlyphStore.

Accepting a candidate writes its em-v1 Sample + sidecars at once; any other status
removes them. The decision log is saved only after the store mirror succeeded.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from hwfont_schema import CaptureSidecar, GlyphStore, Kind, Target

from review.decisions import Decision, DecisionStatus, ReviewLog, save_log
from review.errors import ReviewError
from review.inputs import ReviewInputs
from review.normalize import materialise, sample_id
from review.session import SessionStats

SINGLE_REQUIRED = 12
LIGATURE_REQUIRED = 8


def targets_from_sidecar(sidecar: CaptureSidecar) -> list[Target]:
    """Every expected character as a single target, every ligature target as a ligature; sorted by label."""
    found: dict[str, Target] = {}
    for page in sidecar.pages:
        for region in page.regions:
            for unit in region.expected_units:
                for ch in unit:
                    found.setdefault(ch, Target(label=ch, kind=Kind.single, required_count=SINGLE_REQUIRED))
            for lig in region.ligature_targets:
                found.setdefault(lig, Target(label=lig, kind=Kind.ligature, required_count=LIGATURE_REQUIRED))
    return [found[label] for label in sorted(found)]


def seed_targets(
    store: GlyphStore, inputs: ReviewInputs | CaptureSidecar, targets: Sequence[Target] | None = None
) -> int:
    """Add targets to a store that has none; returns the count added (0 when already seeded).

    `inputs` may be the loaded `ReviewInputs` or just the `CaptureSidecar` (as `review init` has no candidates).
    """
    if store.targets():
        return 0
    sidecar = inputs if isinstance(inputs, CaptureSidecar) else inputs.sidecar
    chosen = list(targets) if targets is not None else targets_from_sidecar(sidecar)
    for t in chosen:
        store.add_target(t)
    return len(chosen)


def mirror(
    store: GlyphStore, inputs: ReviewInputs, stats: SessionStats, log: ReviewLog, candidate_id: str
) -> Decision:
    """Make the store reflect one decision; returns it with flags/quality from materialisation."""
    d = log.decisions.get(candidate_id)
    if d is None:
        raise ReviewError(f"unknown candidate id {candidate_id!r}")
    sid = sample_id(inputs.session_id, candidate_id)
    if d.status is DecisionStatus.accepted:
        m = materialise(inputs, stats, log, candidate_id)  # may raise before any store change
        store.remove_sample(sid)
        store.add_sample(m.sample, strokes=m.strokes, raster=m.raster)
        return d.model_copy(update={"flags": list(m.flags), "quality": m.sample.quality})
    store.remove_sample(sid)
    return d


def apply(
    store: GlyphStore,
    inputs: ReviewInputs,
    stats: SessionStats,
    log: ReviewLog,
    candidate_dir: Path,
    op: Callable[[ReviewLog], ReviewLog],
    candidate_id: str,
) -> tuple[ReviewLog, Decision]:
    """Apply a pure log op, mirror it into the store, then save the log."""
    new_log = op(log)
    d = mirror(store, inputs, stats, new_log, candidate_id)
    new_log = new_log.model_copy(deep=True)
    new_log.decisions[candidate_id] = d
    save_log(candidate_dir, new_log)
    return new_log, d


def replay(store: GlyphStore, inputs: ReviewInputs, stats: SessionStats, log: ReviewLog) -> ReviewLog:
    """Mirror every decision of `log` (sorted by id); returns the updated log without saving it."""
    out = log.model_copy(deep=True)
    for cid in sorted(log.decisions):
        out.decisions[cid] = mirror(store, inputs, stats, log, cid)
    return out

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

from hwfont_schema import BBox, Candidate, Kind, Quality
from pydantic import BaseModel, Field, ValidationError

from review.errors import ReviewError
from review.inputs import ReviewInputs

LOG_NAME = "review.json"
NOT_EXPECTED = "label_not_expected"


class DecisionStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"


class Decision(BaseModel):
    candidate_id: str
    status: DecisionStatus
    label: str
    kind: Kind
    bbox: BBox
    quality: Quality = Quality.good
    flags: list[str] = Field(default_factory=list)
    note: str = ""
    updated_at: str


class ReviewLog(BaseModel):
    version: str = "1"
    session_id: str
    decisions: dict[str, Decision]


def default_decision(candidate: Candidate, now: str) -> Decision:
    return Decision(
        candidate_id=candidate.id,
        status=DecisionStatus.pending,
        label=candidate.label,
        kind=candidate.kind,
        bbox=candidate.bbox.model_copy(),
        quality=Quality.good,
        flags=[],
        note="",
        updated_at=now,
    )


def default_log(inputs: ReviewInputs, now: str) -> ReviewLog:
    decisions = {
        cid: default_decision(c, now) for pg in inputs.pages.values() for cid, c in pg.candidates.items()
    }
    return ReviewLog(session_id=inputs.session_id, decisions=decisions)


def _replace(log: ReviewLog, candidate_id: str, **changes: object) -> ReviewLog:
    if candidate_id not in log.decisions:
        raise ReviewError(f"unknown candidate id {candidate_id!r}")
    new = log.model_copy(deep=True)
    new.decisions[candidate_id] = new.decisions[candidate_id].model_copy(update=changes)
    return new


def accept(log: ReviewLog, candidate_id: str, now: str, quality: Quality | None = None) -> ReviewLog:
    changes: dict[str, object] = {"status": DecisionStatus.accepted, "updated_at": now}
    if quality is not None:
        changes["quality"] = quality
    return _replace(log, candidate_id, **changes)


def reject(log: ReviewLog, candidate_id: str, now: str) -> ReviewLog:
    return _replace(log, candidate_id, status=DecisionStatus.rejected, updated_at=now)


def set_quality(log: ReviewLog, candidate_id: str, quality: Quality, now: str) -> ReviewLog:
    return _replace(log, candidate_id, quality=quality, updated_at=now)


def relabel(log: ReviewLog, inputs: ReviewInputs, candidate_id: str, label: str, now: str) -> ReviewLog:
    if candidate_id not in log.decisions:
        raise ReviewError(f"unknown candidate id {candidate_id!r}")
    if not label or label.strip() != label or any(ch.isspace() for ch in label):
        raise ReviewError(f"invalid label {label!r}: must be non-empty without whitespace")
    cand = inputs.candidate(candidate_id)
    region = inputs.page_of(candidate_id).region(cand.region_id)
    expected = label in region.expected_units or label in region.ligature_targets
    flags = [f for f in log.decisions[candidate_id].flags if f != NOT_EXPECTED]
    if not expected:
        flags.append(NOT_EXPECTED)
    kind = Kind.single if len(label) == 1 else Kind.ligature
    return _replace(log, candidate_id, label=label, kind=kind, flags=flags, updated_at=now)


def adjust_box(log: ReviewLog, inputs: ReviewInputs, candidate_id: str, bbox: BBox, now: str) -> ReviewLog:
    if candidate_id not in log.decisions:
        raise ReviewError(f"unknown candidate id {candidate_id!r}")
    cand = inputs.candidate(candidate_id)
    rb = inputs.page_of(candidate_id).region(cand.region_id).bbox
    # The box must be sized, on the page, and overlap its region (it may overhang the region, e.g. descenders).
    page = inputs.page_of(candidate_id).page
    if bbox.w <= 0 or bbox.h <= 0:
        raise ReviewError(f"box for {candidate_id!r} must have positive width and height")
    if bbox.x < 0 or bbox.y < 0 or bbox.x + bbox.w > page.width_px or bbox.y + bbox.h > page.height_px:
        raise ReviewError(f"box for {candidate_id!r} must lie within the page")
    if not (bbox.x < rb.x + rb.w and bbox.x + bbox.w > rb.x and bbox.y < rb.y + rb.h and bbox.y + bbox.h > rb.y):
        raise ReviewError(f"box for {candidate_id!r} must intersect its region bbox")
    return _replace(log, candidate_id, bbox=bbox.model_copy(), updated_at=now)


def reset(log: ReviewLog, inputs: ReviewInputs, candidate_id: str, now: str) -> ReviewLog:
    if candidate_id not in log.decisions:
        raise ReviewError(f"unknown candidate id {candidate_id!r}")
    new = log.model_copy(deep=True)
    new.decisions[candidate_id] = default_decision(inputs.candidate(candidate_id), now)
    return new


def load_log(candidate_dir: Path, inputs: ReviewInputs, now: str) -> ReviewLog:
    candidate_dir = Path(candidate_dir)
    page = next((pg for pg in inputs.pages.values() if pg.candidate_dir == candidate_dir), None)
    if page is None:
        raise ReviewError(f"candidate directory {candidate_dir} is not among the loaded inputs")
    path = candidate_dir / LOG_NAME
    if not path.exists():
        return ReviewLog(
            session_id=inputs.session_id,
            decisions={cid: default_decision(c, now) for cid, c in page.candidates.items()},
        )
    try:
        log = ReviewLog.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ReviewError(f"cannot read review log {path}: {exc}") from exc
    if log.session_id != inputs.session_id:
        raise ReviewError(
            f"review log {path} belongs to session {log.session_id!r}, not {inputs.session_id!r}"
        )
    for cid in log.decisions:
        if cid not in page.candidates:
            raise ReviewError(f"review log {path} has a decision for unknown candidate {cid!r}")
    for cid, c in page.candidates.items():
        if cid not in log.decisions:
            log.decisions[cid] = default_decision(c, now)
    return log


def save_log(candidate_dir: Path, log: ReviewLog) -> None:
    path = Path(candidate_dir) / LOG_NAME
    tmp = path.with_name(LOG_NAME + ".tmp")
    tmp.write_text(log.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)

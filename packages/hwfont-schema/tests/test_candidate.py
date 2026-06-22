import pytest
from pydantic import ValidationError

from hwfont_schema import BBox, Candidate, CandidateStatus, Context, Kind, PositionInWord


def test_candidate_status_members():
    assert CandidateStatus.pending.value == "pending"
    assert CandidateStatus.needs_review.value == "needs_review"
    assert {s.value for s in CandidateStatus} == {"pending", "needs_review"}


def _candidate(**overrides):
    base = dict(
        id="c1",
        page_id="p0",
        region_id="p0-r0",
        label="a",
        kind=Kind.single,
        confidence=0.8,
        bbox=BBox(x=10, y=20, w=30, h=40),
        context=Context(source_word="cat", position_in_word=PositionInWord.medial),
        status=CandidateStatus.pending,
        alignment_method="fiducial",
        model="claude-opus-4-8",
        created_at="2026-06-22T00:00:00Z",
    )
    base.update(overrides)
    return Candidate(**base)


def test_candidate_roundtrip_and_optional_paths():
    c = _candidate()
    assert c.strokes_path is None and c.crop_path is None
    assert Candidate.model_validate_json(c.model_dump_json()) == c


def test_candidate_confidence_bounds():
    with pytest.raises(ValidationError):
        _candidate(confidence=1.5)
    with pytest.raises(ValidationError):
        _candidate(confidence=-0.1)

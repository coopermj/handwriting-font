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


from hwfont_schema import CandidateProvenance, CandidateSet


def test_candidate_set_roundtrip_and_provenance():
    cs = CandidateSet(
        provenance=CandidateProvenance(
            source_page_id="p0",
            source_raster="page0.png",
            source_svg="page0.svg",
            alignment_method="fiducial",
            alignment_residual_px=1.4,
            model="claude-opus-4-8",
        ),
        candidates=[_candidate(id="c1", confidence=0.9), _candidate(id="c2", confidence=0.2)],
    )
    assert CandidateSet.model_validate_json(cs.model_dump_json()) == cs
    # provenance fields that are unknown for a raster-only run are optional
    prov = CandidateProvenance(
        source_page_id="p0",
        source_raster="page0.png",
        alignment_method="geometric_scale",
        model="claude-opus-4-8",
    )
    assert prov.source_svg is None and prov.alignment_residual_px is None

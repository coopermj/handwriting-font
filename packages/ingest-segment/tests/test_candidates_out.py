import pytest

from hwfont_schema import (
    BBox,
    Candidate,
    CandidateProvenance,
    CandidateStatus,
    Context,
    Kind,
    PositionInWord,
)
from ingest_segment.candidates_out import read_candidate_set, write_candidate_set


def _cand(cid, conf):
    return Candidate(
        id=cid, page_id="p0", region_id="p0-r0", label="a", kind=Kind.single,
        confidence=conf, bbox=BBox(x=0, y=0, w=10, h=10),
        context=Context(source_word="a", position_in_word=PositionInWord.isolated),
        status=CandidateStatus.pending, alignment_method="fiducial",
        model="claude-opus-4-8", created_at="2026-06-22T00:00:00Z",
    )


def _prov():
    return CandidateProvenance(
        source_page_id="p0", source_raster="page0.png",
        alignment_method="fiducial", model="claude-opus-4-8",
    )


def test_write_sorts_low_confidence_first_and_writes_sidecars(tmp_path):
    out = tmp_path / "candidates"
    items = [
        (_cand("hi", 0.9), [[(1.0, 1.0), (2.0, 2.0)]]),
        (_cand("lo", 0.2), []),  # no strokes
    ]
    cs = write_candidate_set(out, _prov(), items, crops={"hi": b"\x89PNG-hi", "lo": b"\x89PNG-lo"})

    assert [c.id for c in cs.candidates] == ["lo", "hi"]  # lowest confidence first
    hi = next(c for c in cs.candidates if c.id == "hi")
    assert hi.strokes_path == "strokes/hi.json"
    assert hi.crop_path == "crop/hi.png"
    lo = next(c for c in cs.candidates if c.id == "lo")
    assert lo.strokes_path is None  # no strokes assigned
    assert (out / "candidates.json").exists()
    assert (out / "strokes" / "hi.json").exists()
    assert (out / "crop" / "lo.png").read_bytes() == b"\x89PNG-lo"


def test_read_roundtrips(tmp_path):
    out = tmp_path / "candidates"
    items = [(_cand("c1", 0.5), [])]
    written = write_candidate_set(out, _prov(), items, crops={"c1": b"x"})
    loaded = read_candidate_set(out)
    assert loaded == written


def test_refuses_overwrite_without_force(tmp_path):
    out = tmp_path / "candidates"
    write_candidate_set(out, _prov(), [(_cand("c1", 0.5), [])], crops={"c1": b"x"})
    with pytest.raises(FileExistsError):
        write_candidate_set(out, _prov(), [(_cand("c1", 0.5), [])], crops={"c1": b"x"})
    # force overwrites
    write_candidate_set(out, _prov(), [(_cand("c1", 0.5), [])], crops={"c1": b"x"}, force=True)

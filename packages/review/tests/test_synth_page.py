from __future__ import annotations

from hwfont_schema import CandidateSet, CandidateStatus, Context, Kind, PositionInWord
from PIL import Image

from synth_page import synth_page


def test_synth_page_writes_ingest_format(tmp_path):
    sp = synth_page(tmp_path, ["the cat", "a hat"], seed=3)
    cs = CandidateSet.model_validate_json((sp.candidate_dir / "candidates.json").read_text())
    assert cs.provenance.source_page_id == sp.page.id == "p0"
    assert [c.confidence for c in cs.candidates] == sorted(c.confidence for c in cs.candidates)
    assert {c.region_id for c in cs.candidates} == {r.id for r in sp.page.regions}
    c = next(c for c in cs.candidates if c.label == "h" and c.region_id == sp.page.regions[0].id)
    assert c.context == Context(source_word="the", left_neighbor="t", right_neighbor="e", position_in_word=PositionInWord.medial)
    assert (sp.candidate_dir / c.strokes_path).exists() and (sp.candidate_dir / c.crop_path).exists()
    assert Image.open(sp.raster_path).size == (sp.page.width_px, sp.page.height_px)
    region = sp.page.regions[0]
    assert region.bbox.h == 70 and region.baseline_y == region.bbox.y + 70
    for x, y in sp.ink[c.id][0]:
        assert c.bbox.x <= x <= c.bbox.x + c.bbox.w and c.bbox.y <= y <= c.bbox.y + c.bbox.h


def test_synth_page_ligature_option_and_needs_review(tmp_path):
    sp = synth_page(tmp_path, ["the cat"], ligatures={0: [(0, "th")]})
    cs = sp.candidate_set
    th = next(c for c in cs.candidates if c.label == "th")
    assert th.kind == Kind.ligature and th.status == CandidateStatus.needs_review
    assert len(cs.candidates) == 5  # th, e, c, a, t
    assert th.context.source_word == "the" and th.context.right_neighbor == "e"

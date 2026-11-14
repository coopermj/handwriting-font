from __future__ import annotations

import shutil

import pytest
from hwfont_schema import CandidateSet
from PIL import Image

from review import ReviewError
from review.inputs import load_inputs


def test_load_inputs_reads_everything(synth):
    inputs = load_inputs(synth.sidecar_path, [synth.candidate_dir], {"p0": synth.raster_path})
    assert inputs.session_id == "capture" and set(inputs.pages) == {"p0"}
    pg = inputs.pages["p0"]
    assert set(pg.candidates) == {c.id for c in synth.candidate_set.candidates}
    assert set(pg.strokes) == set(pg.candidates) and set(pg.crops) == set(pg.candidates)
    assert pg.raster.mode == "L" and pg.raster.size == (pg.page.width_px, pg.page.height_px)
    r0 = synth.page.regions[0].id
    assert pg.candidate_ids(r0) == tuple(c.id for c in synth.candidate_set.candidates if c.region_id == r0)
    assert inputs.page_of(next(iter(pg.candidates))) is pg


def test_load_inputs_without_raster(synth):
    inputs = load_inputs(synth.sidecar_path, [synth.candidate_dir])
    assert inputs.pages["p0"].raster is None


def test_unknown_page_duplicate_page_and_raster_size_are_errors(synth, tmp_path):
    bad = CandidateSet.model_validate_json((synth.candidate_dir / "candidates.json").read_text())
    bad = bad.model_copy(update={"provenance": bad.provenance.model_copy(update={"source_page_id": "p9"})})
    other = tmp_path / "other"
    shutil.copytree(synth.candidate_dir, other)
    (other / "candidates.json").write_text(bad.model_dump_json())
    with pytest.raises(ReviewError, match="p9"):
        load_inputs(synth.sidecar_path, [other])
    with pytest.raises(ReviewError, match="twice"):
        load_inputs(synth.sidecar_path, [synth.candidate_dir, synth.candidate_dir])
    small = tmp_path / "small.png"
    Image.new("L", (10, 10), 255).save(small)
    with pytest.raises(ReviewError, match="size"):
        load_inputs(synth.sidecar_path, [synth.candidate_dir], {"p0": small})


def test_missing_stroke_file_is_an_error(synth):
    victim = next(c for c in synth.candidate_set.candidates if c.strokes_path)
    (synth.candidate_dir / victim.strokes_path).unlink()
    with pytest.raises(ReviewError, match=victim.id):
        load_inputs(synth.sidecar_path, [synth.candidate_dir])

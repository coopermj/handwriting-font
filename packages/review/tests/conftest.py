from __future__ import annotations

import pytest
from hwfont_schema import GlyphStore

from review.inputs import ReviewInputs, load_inputs
from synth_page import SynthPage, synth_page

NOW = "2026-11-14T10:00:00Z"


@pytest.fixture
def synth(tmp_path) -> SynthPage:
    return synth_page(
        tmp_path / "synth",
        ["the cat sat", "a fat hen ate", "the hat"],
        ligatures={2: [(0, "th")]},
    )


@pytest.fixture
def store(tmp_path):
    s = GlyphStore.create(tmp_path / "store")
    yield s
    s.close()


@pytest.fixture
def inputs(synth) -> ReviewInputs:
    return load_inputs(synth.sidecar_path, [synth.candidate_dir], {"p0": synth.raster_path})


def _id_of(inputs: ReviewInputs, label: str, region: int) -> str:
    """Id of the leftmost (original bbox.x) candidate labelled `label` in page p0's region `region`.

    """
    pg = inputs.pages["p0"]
    region_id = pg.page.regions[region].id
    matches = [c for c in pg.candidates.values() if c.region_id == region_id and c.label == label]
    if not matches:
        raise KeyError(f"no candidate labelled {label!r} in region {region}")
    return min(matches, key=lambda c: c.bbox.x).id

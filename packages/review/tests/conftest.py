from __future__ import annotations

from typing import Any

import pytest
from hwfont_schema import GlyphStore

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


def _id_of(inputs: Any, label: str, region: int) -> str:
    """Id of the leftmost (original bbox.x) candidate labelled `label` in page p0's region `region`.

    `inputs` is a ReviewInputs (defined in a later task): anything with
    `.pages["p0"].candidates: dict[str, Candidate]` and `.pages["p0"].page.regions`.
    """
    pg = inputs.pages["p0"]
    region_id = pg.page.regions[region].id
    matches = [c for c in pg.candidates.values() if c.region_id == region_id and c.label == label]
    if not matches:
        raise KeyError(f"no candidate labelled {label!r} in region {region}")
    return min(matches, key=lambda c: c.bbox.x).id

from __future__ import annotations

from pathlib import Path

import pytest

from font_gen.synth import synth_store

FIXTURE_SPEC = {
    "a": {"medi": 5, "fina": 3, "init": 1}, "z": {"medi": 2}, "Q": {"isol": 1},
    "fi": {"medi": 4}, "ing": {"fina": 3}, "tion": {"fina": 2}, "ff": {"fina": 3}, "st": {"fina": 3},
    "f": {"medi": 3}, "i": {"medi": 3}, "n": {"medi": 3}, "g": {"medi": 3}, "t": {"medi": 3},
    "o": {"medi": 3}, "s": {"medi": 3},
}
E2E_EXTRAS = {"q": {"medi": 2}, "x": {"medi": 1}, "b": {"medi": 12}, "e": {"medi": 2}, "h": {"medi": 3}}  # h: so "the the the" uses real letters


def pytest_addoption(parser):
    parser.addoption("--update-goldens", action="store_true", default=False)


@pytest.fixture(scope="session")
def update_goldens(request) -> bool:
    return request.config.getoption("--update-goldens")


@pytest.fixture(scope="session")
def goldens() -> Path:
    return Path(__file__).parent / "goldens"


def assert_golden(path: Path, text: str, update: bool) -> None:
    if update:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == text


@pytest.fixture(scope="session")
def fixture_store(tmp_path_factory):
    store = synth_store(tmp_path_factory.mktemp("fixture") / "store", FIXTURE_SPEC, seed=1)
    yield store
    store.close()


@pytest.fixture(scope="session")
def e2e_store(tmp_path_factory):
    store = synth_store(
        tmp_path_factory.mktemp("e2e") / "store", {**FIXTURE_SPEC, **E2E_EXTRAS}, seed=1,
        raster_only=frozenset({"q"}), degenerate=frozenset({"x"}), bad={"b": 10}, marginal={"z": 1},
        kind_mismatch=frozenset({"e"}), missing_targets=frozenset({"k"}),
    )
    yield store
    store.close()


@pytest.fixture(scope="session")
def fixture_loaded(fixture_store):
    from font_gen.load import load_store
    return load_store(fixture_store)


@pytest.fixture(scope="session")
def fixture_plan(fixture_loaded):
    from font_gen.select import plan_glyphs
    return plan_glyphs(fixture_loaded)[0]


@pytest.fixture(scope="session")
def fixture_samples(fixture_loaded):
    from font_gen.select import sample_index
    return sample_index(fixture_loaded)


@pytest.fixture(scope="session")
def fixture_outlines(fixture_plan, fixture_samples):
    from font_gen.outline import Outline, notdef_outline, outline_from_strokes
    out = {".notdef": notdef_outline(), "space": Outline.empty()}
    for g in fixture_plan.glyphs:
        if g.sample_id is not None:
            ls = fixture_samples[g.sample_id]
            out[g.name] = outline_from_strokes(ls.strokes, ls.sample.metrics, glyph=g.name).outline
    return out

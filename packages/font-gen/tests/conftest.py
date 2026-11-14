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


def build_stub_font(plan, fea_text: str) -> bytes:
    """A CFF stub font with a box outline per glyph, used to compile and shape generated features."""
    from io import BytesIO

    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    names = plan.names()
    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap(plan.cmap)
    charstrings = {}
    for n in names:
        pen = T2CharStringPen(500, None)
        if n != "space":
            pen.moveTo((50, 0))
            pen.lineTo((450, 0))
            pen.lineTo((450, 500))
            pen.lineTo((50, 500))
            pen.closePath()
        charstrings[n] = pen.getCharString()
    fb.setupCFF("Stub", {"FullName": "Stub", "FamilyName": "Stub"}, charstrings, {})
    fb.setupHorizontalMetrics({n: (500, 50 if n != "space" else 0) for n in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Stub", "styleName": "Regular"})
    fb.setupOS2(version=4, sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200, fsSelection=0xC0)
    fb.setupPost()
    fb.addOpenTypeFeatures(fea_text)
    buf = BytesIO()
    fb.save(buf)
    return buf.getvalue()

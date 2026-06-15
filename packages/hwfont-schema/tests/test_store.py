from pathlib import Path

from hwfont_schema.enums import Kind, PositionInWord, Quality, ReviewStatus
from hwfont_schema.geometry import BBox
from hwfont_schema.sample import Context, Metrics, Sample, Target
from hwfont_schema.store import GlyphStore
from hwfont_schema.strokes import Contour, StrokeData, StrokePoint


def _sample(sample_id: str, label: str, position=PositionInWord.medial) -> Sample:
    return Sample(
        id=sample_id,
        label=label,
        kind=Kind.single,
        context=Context(source_word="cat", position_in_word=position),
        metrics=Metrics(baseline=0.0, x_height=0.5, advance=0.6, bbox=BBox(x=0, y=0, w=0.5, h=0.5)),
        quality=Quality.good,
        review_status=ReviewStatus.accepted,
        capture_session_id="sess-1",
        created_at="2026-06-15T00:00:00Z",
    )


def _strokes() -> StrokeData:
    return StrokeData(contours=[Contour(points=[StrokePoint(x=0, y=0), StrokePoint(x=1, y=1)])])


def test_create_initializes_directory_layout(tmp_path: Path):
    store = GlyphStore.create(tmp_path / "store")
    assert (tmp_path / "store" / "store.db").exists()
    assert (tmp_path / "store" / "strokes").is_dir()
    assert (tmp_path / "store" / "raster").is_dir()
    store.close()


def test_add_sample_writes_stroke_sidecar_and_sets_relative_path(tmp_path: Path):
    store = GlyphStore.create(tmp_path / "store")
    stored = store.add_sample(_sample("s1", "a"), strokes=_strokes(), raster=b"\x89PNG_fake")
    store.close()

    assert stored.strokes_path == "strokes/s1.json"
    assert stored.raster_path == "raster/s1.png"
    assert (tmp_path / "store" / "strokes" / "s1.json").exists()
    assert (tmp_path / "store" / "raster" / "s1.png").read_bytes() == b"\x89PNG_fake"


def test_reopen_and_read_sample_back(tmp_path: Path):
    store = GlyphStore.create(tmp_path / "store")
    store.add_sample(_sample("s1", "a"), strokes=_strokes(), raster=None)
    store.close()

    reopened = GlyphStore.open(tmp_path / "store")
    got = reopened.samples_for("a")
    reopened.close()

    assert len(got) == 1
    assert got[0].id == "s1"
    assert got[0].label == "a"

from pathlib import Path

import hwfont_schema as hs


def test_top_level_exports_present():
    for name in [
        "BBox",
        "StrokeData",
        "Contour",
        "StrokePoint",
        "CaptureSidecar",
        "Page",
        "Region",
        "Sample",
        "Target",
        "Context",
        "Metrics",
        "Kind",
        "PositionInWord",
        "ReviewStatus",
        "Quality",
        "GlyphStore",
        "CoverageRow",
    ]:
        assert hasattr(hs, name), f"missing export: {name}"


def test_end_to_end_sidecar_and_store(tmp_path: Path):
    sidecar = hs.CaptureSidecar(
        pages=[
            hs.Page(
                id="p1",
                width_px=1404,
                height_px=1872,
                dpi=226,
                regions=[
                    hs.Region(
                        id="r1",
                        expected_transcript="cat",
                        baseline_y=100.0,
                        bbox=hs.BBox(x=0, y=60, w=300, h=60),
                        expected_units=["c", "a", "t"],
                    )
                ],
            )
        ]
    )
    assert hs.CaptureSidecar.model_validate_json(sidecar.model_dump_json()) == sidecar

    store = hs.GlyphStore.create(tmp_path / "store")
    store.add_target(hs.Target(label="a", kind=hs.Kind.single, required_count=1))
    store.add_sample(
        hs.Sample(
            id="s1",
            label="a",
            kind=hs.Kind.single,
            context=hs.Context(source_word="cat", position_in_word=hs.PositionInWord.medial),
            metrics=hs.Metrics(
                baseline=0.0, x_height=0.5, advance=0.6, bbox=hs.BBox(x=0, y=0, w=0.5, h=0.5)
            ),
            quality=hs.Quality.good,
            review_status=hs.ReviewStatus.accepted,
            capture_session_id="sess-1",
            created_at="2026-06-15T00:00:00Z",
        ),
        strokes=hs.StrokeData(
            contours=[hs.Contour(points=[hs.StrokePoint(x=0, y=0), hs.StrokePoint(x=1, y=1)])]
        ),
    )
    coverage = store.coverage()
    store.close()

    assert coverage[0].met is True

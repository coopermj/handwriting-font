from hwfont_schema import PositionInWord
from ingest_segment.segment import derive_context

TRANSCRIPT = "the cat"  # units: t,h,e,c,a,t  -> indices 0..5


def test_context_medial_with_neighbors():
    ctx = derive_context(TRANSCRIPT, "a", unit_index=4)  # 'a' in "cat"
    assert ctx.source_word == "cat"
    assert ctx.position_in_word == PositionInWord.medial
    assert ctx.left_neighbor == "c"
    assert ctx.right_neighbor == "t"


def test_context_initial_and_final():
    initial = derive_context(TRANSCRIPT, "c", unit_index=3)  # 'c' starts "cat"
    assert initial.position_in_word == PositionInWord.initial
    assert initial.left_neighbor is None and initial.right_neighbor == "a"

    final = derive_context(TRANSCRIPT, "e", unit_index=2)  # 'e' ends "the"
    assert final.position_in_word == PositionInWord.final
    assert final.left_neighbor == "h" and final.right_neighbor is None


def test_context_isolated_single_char_word():
    ctx = derive_context("a cat", "a", unit_index=0)  # word "a"
    assert ctx.position_in_word == PositionInWord.isolated
    assert ctx.left_neighbor is None and ctx.right_neighbor is None


def test_context_ligature_span():
    th = derive_context("the", "th", unit_index=0, length=2)
    assert th.source_word == "the"
    assert th.position_in_word == PositionInWord.initial
    assert th.left_neighbor is None and th.right_neighbor == "e"

    whole = derive_context("the cat", "the", unit_index=0, length=3)
    assert whole.position_in_word == PositionInWord.isolated
    assert whole.left_neighbor is None and whole.right_neighbor is None

    mid = derive_context("that", "ha", unit_index=1, length=2)
    assert mid.position_in_word == PositionInWord.medial
    assert mid.left_neighbor == "t" and mid.right_neighbor == "t"


def test_segment_region_contexts_after_ligature_box():
    """A `th` box followed by `e` in "the": contexts use cumulative character offsets, not box indices."""
    from PIL import Image

    from hwfont_schema import BBox, Region
    from ingest_segment.segment import VisionBox, VisionResult, segment_region

    region = Region(
        id="p0-r0",
        expected_transcript="the hat",
        baseline_y=60.0,
        bbox=BBox(x=10.0, y=20.0, w=200.0, h=40.0),
        expected_units=["t", "h", "e", "h", "a", "t"],
        ligature_targets=["th"],
    )

    def vision(crop_png, region):
        return VisionResult(boxes=[
            VisionBox(label="th", kind="ligature", x=0, y=0, w=40, h=40, confidence=0.9),
            VisionBox(label="e", kind="single", x=45, y=0, w=20, h=40, confidence=0.9),
            VisionBox(label="h", kind="single", x=90, y=0, w=20, h=40, confidence=0.9),
            VisionBox(label="a", kind="single", x=115, y=0, w=20, h=40, confidence=0.9),
            VisionBox(label="t", kind="single", x=140, y=0, w=20, h=40, confidence=0.9),
        ])

    results = segment_region(
        region=region,
        raster=Image.new("L", (300, 200), color=255),
        page_strokes=[],
        vision=vision,
        page_id="p0",
        alignment_method="fiducial",
        page_low_confidence=False,
        model="claude-opus-4-8",
        created_at="2026-06-22T00:00:00Z",
    )
    ctx = [c.context for c, _ in results]
    assert ctx[0].right_neighbor == "e" and ctx[0].left_neighbor is None
    assert ctx[0].position_in_word == PositionInWord.initial
    assert ctx[1].left_neighbor == "h" and ctx[1].right_neighbor is None
    assert ctx[1].position_in_word == PositionInWord.final and ctx[1].source_word == "the"
    assert ctx[2].source_word == "hat" and ctx[2].position_in_word == PositionInWord.initial
    assert ctx[2].left_neighbor is None and ctx[2].right_neighbor == "a"
    assert ctx[4].position_in_word == PositionInWord.final and ctx[4].left_neighbor == "a"

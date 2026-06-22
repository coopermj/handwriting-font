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

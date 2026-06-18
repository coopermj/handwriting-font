from capture_template.text_wrap import wrap_text


def test_wrap_empty_is_empty_list():
    assert wrap_text("", 10) == []
    assert wrap_text("   ", 10) == []


def test_wrap_short_text_is_one_segment():
    assert wrap_text("a cat sat", 20) == ["a cat sat"]


def test_wrap_breaks_on_word_boundaries_within_budget():
    # budget 10: "the quick" (9) fits; adding " brown" (15) doesn't
    assert wrap_text("the quick brown fox", 10) == ["the quick", "brown fox"]


def test_wrap_lone_overlong_word_gets_its_own_line():
    assert wrap_text("a supercalifragilistic b", 6) == ["a", "supercalifragilistic", "b"]


def test_wrap_is_deterministic():
    text = "one two three four five six seven eight"
    assert wrap_text(text, 12) == wrap_text(text, 12)

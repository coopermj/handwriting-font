from capture_template.planner import count_occurrences


def test_count_occurrences_single_char():
    assert count_occurrences("banana", "a") == 3


def test_count_occurrences_non_overlapping_substring():
    # "aa" in "aaaa" is 2 non-overlapping, not 3
    assert count_occurrences("aaaa", "aa") == 2


def test_count_occurrences_ligature():
    assert count_occurrences("left often after", "eft") == 1
    assert count_occurrences("effect", "eft") == 0

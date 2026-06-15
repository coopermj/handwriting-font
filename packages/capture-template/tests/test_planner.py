from capture_template.planner import count_occurrences


def test_count_occurrences_single_char():
    assert count_occurrences("banana", "a") == 3


def test_count_occurrences_non_overlapping_substring():
    # "aa" in "aaaa" is 2 non-overlapping, not 3
    assert count_occurrences("aaaa", "aa") == 2


def test_count_occurrences_ligature():
    assert count_occurrences("left often after", "eft") == 1
    assert count_occurrences("effect", "eft") == 0


from hwfont_schema import Kind, Target

from capture_template.planner import PlanResult, PromptLine, plan


def test_plan_selects_natural_sentences_to_meet_coverage():
    targets = [Target(label="a", kind=Kind.single, required_count=2)]
    candidates = ["banana bread", "zzz", "a"]
    result = plan(targets, candidates)
    assert isinstance(result, PlanResult)
    assert result.all_met is True
    # the banana sentence alone supplies 3 a's; planner should pick it first and stop
    assert result.lines[0] == PromptLine(text="banana bread", is_drill=False)
    assert all(line.is_drill is False for line in result.lines)


def test_plan_is_deterministic_with_stable_tie_breaking():
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    candidates = ["cat", "bat"]  # both supply exactly one 'a'; tie
    first = plan(targets, candidates).lines
    second = plan(targets, candidates).lines
    assert first == second
    # tie broken by (shorter, then lexicographic, then index): "bat" and "cat" same length -> lexicographic
    assert first[0].text == "bat"

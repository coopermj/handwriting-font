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


def test_plan_drill_fills_rare_ligature_absent_from_corpus():
    # corpus has no "eft"; planner must drill-fill it and flag the line as a drill
    targets = [Target(label="eft", kind=Kind.ligature, required_count=3)]
    candidates = ["the cat sat", "a dog ran"]
    result = plan(targets, candidates)
    assert result.all_met is True
    drills = [line for line in result.lines if line.is_drill]
    assert drills, "expected at least one drill line"
    assert "eft" in drills[0].text
    cov = {r.label: r for r in result.coverage}
    assert cov["eft"].source == "drill"
    assert cov["eft"].met is True


def test_plan_reports_unmet_target_when_drill_budget_too_small():
    # a ligature longer than the drill budget can never be placed -> unmet, not dropped
    targets = [Target(label="abcdefgh", kind=Kind.ligature, required_count=1)]
    result = plan(targets, [], drill_budget=4)
    assert result.all_met is False
    cov = {r.label: r for r in result.coverage}
    assert cov["abcdefgh"].met is False
    assert cov["abcdefgh"].source == "none"

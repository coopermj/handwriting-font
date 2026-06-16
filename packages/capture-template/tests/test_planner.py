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


def test_plan_respects_line_cap_across_drill_fill():
    # many distinct targets, tiny line_cap, empty corpus -> drill phase must stop at the cap
    targets = [Target(label=ch, kind=Kind.single, required_count=1) for ch in "abcde"]
    result = plan(targets, [], line_cap=2)
    assert len(result.lines) == 2
    assert result.all_met is False  # cap reached before all targets drilled


def test_plan_without_target_lines_is_unchanged():
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    result = plan(targets, ["cat", "bat", "rat"])  # coverage met by one line; no fill
    assert len(result.lines) == 1
    assert all(not line.is_drill for line in result.lines)


def test_plan_variety_fill_reaches_target_lines_with_genuine_only():
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    candidates = ["cat", "bat", "rat", "mat", "hat", "sat", "fat", "pat"]
    result = plan(targets, candidates, target_lines=5)
    assert len(result.lines) == 5
    assert all(not line.is_drill for line in result.lines)


def test_plan_variety_fill_stops_at_pool_exhaustion_without_padding():
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    result = plan(targets, ["cat", "bat"], target_lines=10)
    assert len(result.lines) == 2  # only two genuine available; not padded to 10
    assert all(not line.is_drill for line in result.lines)


def test_plan_variety_fill_prefers_more_novel_sentences():
    targets = [Target(label="x", kind=Kind.single, required_count=1)]
    # coverage met by "xx"; fill then prefers the sentence adding the most new bigrams
    result = plan(targets, ["xx", "abcd", "ab"], target_lines=2)
    assert result.lines[0].text == "xx"
    assert result.lines[1].text == "abcd"  # 3 new bigrams (ab,bc,cd) beats "ab" (1)


def test_plan_coverage_wins_over_small_target_lines():
    # base coverage needs more lines than target_lines=1 can hold; coverage still met
    targets = [
        Target(label="a", kind=Kind.single, required_count=1),
        Target(label="z", kind=Kind.single, required_count=1),
    ]
    result = plan(targets, ["aaa", "zzz"], target_lines=1)
    assert result.all_met is True
    assert len(result.lines) >= 2  # one line per letter, target_lines floor doesn't cap coverage

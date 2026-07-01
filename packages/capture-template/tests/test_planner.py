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


def test_plan_drill_fill_ignores_line_cap_for_mandatory_coverage():
    # many distinct targets, tiny line_cap, empty corpus -> drills are mandatory
    # coverage and are NOT bounded by the cap: every target must still be covered.
    targets = [Target(label=ch, kind=Kind.single, required_count=1) for ch in "abcde"]
    result = plan(targets, [], line_cap=2)
    assert len(result.lines) == 5
    assert result.all_met is True  # coverage-first: all targets drilled despite cap


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


def test_plan_target_lines_can_exceed_line_cap():
    # target_lines above line_cap raises the effective cap, so genuine variety-fill
    # grows the booklet past line_cap.
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    candidates = [f"a sample sentence number {n} here" for n in range(60)]
    result = plan(targets, candidates, line_cap=10, target_lines=25)
    assert len(result.lines) == 25
    assert all(not line.is_drill for line in result.lines)


def test_plan_reserves_drill_room_when_target_exceeds_line_cap():
    # Large genuine pool + target_lines above line_cap must NOT starve the coverage
    # drills a rare target needs. (Corpus has no 'q', so 'q' requires a drill.)
    targets = [
        Target(label="a", kind=Kind.single, required_count=1),
        Target(label="q", kind=Kind.single, required_count=3),
    ]
    candidates = [f"a sample sentence number {n}" for n in range(50)]
    result = plan(targets, candidates, line_cap=10, target_lines=30)
    assert result.all_met is True  # coverage drills for 'q' were reserved, not crowded out
    assert any(line.is_drill for line in result.lines)


from capture_template.text_wrap import wrap_text


def test_plan_target_lines_counts_rendered_rows_not_entries():
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    # each candidate wraps to 2 rows at max_line_chars=2 ("aa bb" -> ["aa","bb"])
    candidates = ["aa bb", "aa cc", "aa dd", "aa ee"]
    result = plan(targets, candidates, target_lines=4, max_line_chars=2)
    rendered = sum(len(wrap_text(line.text, 2)) for line in result.lines)
    assert rendered == 4            # rendered rows respect target_lines
    assert len(result.lines) == 2   # two entries, each costing 2 rows
    assert all(not line.is_drill for line in result.lines)


def test_plan_max_line_chars_none_is_one_row_per_entry():
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    # without max_line_chars, behavior is unchanged (each entry costs one row)
    result = plan(targets, ["aa bb cc dd ee ff"], target_lines=1)
    assert len(result.lines) == 1


from capture_template.planner import plan
from hwfont_schema import Kind, Target


def _wraps_to_two_rows_with_one_a(i: int) -> str:
    # "a" + 7 filler + space + 8 filler -> exactly one 'a', 16 chars -> 2 rows at mlc=8
    return "a" + chr(98 + i) * 7 + " " + chr(112 + i) * 8


def test_coverage_met_at_narrow_width_despite_small_row_budget():
    # Regression: narrow rows cost more rows/sentence; a small effective_cap must
    # NOT stop mandatory coverage. Pre-fix this yields achieved 'a' == 3 (unmet).
    targets = [Target(label="a", kind=Kind.single, required_count=6)]
    candidates = [_wraps_to_two_rows_with_one_a(i) for i in range(6)]
    result = plan(targets, candidates, line_cap=6, target_lines=6, max_line_chars=8)
    assert result.all_met is True
    row = next(r for r in result.coverage if r.label == "a")
    assert row.achieved >= 6


def test_coverage_is_width_independent():
    # Same targets/candidates/budget at a narrow vs wide width -> both fully covered.
    targets = [Target(label="a", kind=Kind.single, required_count=6)]
    candidates = [_wraps_to_two_rows_with_one_a(i) for i in range(6)]
    narrow = plan(targets, candidates, line_cap=6, target_lines=6, max_line_chars=8)
    wide = plan(targets, candidates, line_cap=6, target_lines=6, max_line_chars=80)
    assert narrow.all_met is True and wide.all_met is True


def test_drill_fill_covers_target_absent_from_pool_uncapped():
    # 'z' appears in no candidate -> only drills can cover it. Small budget must not
    # starve Phase 3. Pre-fix: rendered >= effective_cap skips drills -> unmet.
    targets = [Target(label="z", kind=Kind.single, required_count=4)]
    candidates = ["hello world", "foo bar qux nope", "lorem ipsum dolor"]  # no 'z'
    result = plan(targets, candidates, line_cap=2, target_lines=2, max_line_chars=8)
    row = next(r for r in result.coverage if r.label == "z")
    assert row.met is True
    assert row.source == "drill"


def test_variety_fill_still_honors_target_lines_floor():
    # Coverage needs 1 line; a larger target_lines must still pull in variety sentences.
    targets = [Target(label="a", kind=Kind.single, required_count=1)]
    candidates = [f"alpha {chr(98 + i)}{chr(98 + i)}{chr(98 + i)}" for i in range(12)]
    result = plan(targets, candidates, line_cap=200, target_lines=8, max_line_chars=80)
    assert result.all_met is True
    assert len(result.lines) >= 5  # variety-fill grew the booklet toward the target

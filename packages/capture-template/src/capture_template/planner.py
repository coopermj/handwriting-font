from __future__ import annotations

from dataclasses import dataclass, field

from hwfont_schema import Kind, Target


def count_occurrences(text: str, label: str) -> int:
    """Non-overlapping, left-to-right occurrences of `label` in `text`."""
    if not label:
        return 0
    count = 0
    start = 0
    while True:
        idx = text.find(label, start)
        if idx == -1:
            return count
        count += 1
        start = idx + len(label)


@dataclass
class PromptLine:
    text: str
    is_drill: bool


@dataclass
class CoverageRow:
    label: str
    kind: Kind
    required: int
    achieved: int
    source: str  # "natural" | "drill" | "none"
    met: bool


@dataclass
class PlanResult:
    lines: list[PromptLine] = field(default_factory=list)
    coverage: list[CoverageRow] = field(default_factory=list)
    all_met: bool = False


def _score(text: str, targets: list[Target], deficit: dict[str, int]) -> int:
    return sum(
        min(count_occurrences(text, t.label), deficit[t.label])
        for t in targets
        if deficit[t.label] > 0
    )


def _drill_lines(label: str, needed: int, drill_budget: int) -> list[str]:
    """Lines repeating `label` (space-separated), each within `drill_budget` chars.

    Returns [] if a single occurrence cannot fit the budget (label longer than budget)."""
    if len(label) > drill_budget or needed <= 0:
        return []
    per_line = max(1, (drill_budget + 1) // (len(label) + 1))  # tokens that fit "lab lab ..."
    lines: list[str] = []
    remaining = needed
    while remaining > 0:
        take = min(per_line, remaining)
        lines.append(" ".join([label] * take))
        remaining -= take
    return lines


def _bigrams(text: str) -> set[tuple[str, str]]:
    """Adjacent non-space character pairs — a proxy for glyph-in-context coverage."""
    return {(a, b) for a, b in zip(text, text[1:]) if a != " " and b != " "}


def _words(text: str) -> set[str]:
    return set(text.split())


def plan(
    targets: list[Target],
    candidates: list[str],
    line_cap: int = 200,
    drill_budget: int = 60,
    target_lines: int | None = None,
) -> PlanResult:
    deficit = {t.label: t.required_count for t in targets}
    achieved_natural = {t.label: 0 for t in targets}
    achieved_drill = {t.label: 0 for t in targets}

    effective_cap = max(line_cap, target_lines) if target_lines is not None else line_cap

    pool = list(enumerate(candidates))
    lines: list[PromptLine] = []

    # Phase 1 — coverage: meet base counts with genuine sentences.
    while len(lines) < effective_cap and any(d > 0 for d in deficit.values()):
        best = None
        for idx, text in pool:
            score = _score(text, targets, deficit)
            if score <= 0:
                continue
            key = (-score, len(text), text, idx)
            if best is None or key < best[0]:
                best = (key, idx, text)
        if best is None:
            break
        _, idx, text = best
        pool = [(i, t) for (i, t) in pool if i != idx]
        lines.append(PromptLine(text=text, is_drill=False))
        for t in targets:
            occ = count_occurrences(text, t.label)
            achieved_natural[t.label] += occ
            deficit[t.label] = max(0, deficit[t.label] - occ)

    # Phase 2 — variety-fill: add genuine sentences (never drills) up to target_lines,
    # choosing the most context-novel candidate each step (new bigrams, then new words).
    if target_lines is not None:
        seen_bigrams: set[tuple[str, str]] = set()
        seen_words: set[str] = set()
        for line in lines:
            seen_bigrams |= _bigrams(line.text)
            seen_words |= _words(line.text)
        while len(lines) < target_lines and pool:
            best = None
            for idx, text in pool:
                key = (
                    -len(_bigrams(text) - seen_bigrams),
                    -len(_words(text) - seen_words),
                    len(text),
                    text,
                    idx,
                )
                if best is None or key < best[0]:
                    best = (key, idx, text)
            _, idx, text = best
            pool = [(i, t) for (i, t) in pool if i != idx]
            lines.append(PromptLine(text=text, is_drill=False))
            seen_bigrams |= _bigrams(text)
            seen_words |= _words(text)
            for t in targets:
                occ = count_occurrences(text, t.label)
                achieved_natural[t.label] += occ
                deficit[t.label] = max(0, deficit[t.label] - occ)

    # Phase 3 — drill-fill remaining base-coverage gaps, respecting effective_cap.
    for t in targets:
        if len(lines) >= effective_cap:
            break
        if deficit[t.label] <= 0:
            continue
        for drill_text in _drill_lines(t.label, deficit[t.label], drill_budget):
            if len(lines) >= effective_cap:
                break
            lines.append(PromptLine(text=drill_text, is_drill=True))
            occ = count_occurrences(drill_text, t.label)
            achieved_drill[t.label] += occ
            deficit[t.label] = max(0, deficit[t.label] - occ)

    coverage = _coverage(targets, achieved_natural, achieved_drill)
    return PlanResult(lines=lines, coverage=coverage, all_met=all(r.met for r in coverage))


def _coverage(
    targets: list[Target],
    achieved_natural: dict[str, int],
    achieved_drill: dict[str, int],
) -> list[CoverageRow]:
    rows: list[CoverageRow] = []
    for t in targets:
        nat = achieved_natural[t.label]
        total = nat + achieved_drill[t.label]
        if nat >= t.required_count:
            source = "natural"
        elif total >= t.required_count:
            source = "drill"
        else:
            source = "none"
        rows.append(
            CoverageRow(
                label=t.label,
                kind=t.kind,
                required=t.required_count,
                achieved=total,
                source=source,
                met=total >= t.required_count,
            )
        )
    return rows

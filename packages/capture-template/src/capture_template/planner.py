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


def plan(targets: list[Target], candidates: list[str], line_cap: int = 200) -> PlanResult:
    deficit = {t.label: t.required_count for t in targets}
    achieved_natural = {t.label: 0 for t in targets}

    pool = list(enumerate(candidates))  # (original_index, text)
    lines: list[PromptLine] = []

    while len(lines) < line_cap and any(d > 0 for d in deficit.values()):
        best = None  # (neg_score, length, text, index)
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

    coverage = _coverage(targets, achieved_natural, {t.label: 0 for t in targets})
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

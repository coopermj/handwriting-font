from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from hwfont_schema import GlyphStore, Kind

from review.decisions import DecisionStatus, ReviewLog


@dataclass(frozen=True)
class CoverageLine:
    label: str
    kind: Kind
    required: int
    accepted: int
    pending: int
    met: bool


@dataclass(frozen=True)
class CoverageReport:
    lines: tuple[CoverageLine, ...]
    untargeted: tuple[tuple[str, int], ...]
    met_count: int
    total: int


def report(store: GlyphStore, logs: Sequence[ReviewLog]) -> CoverageReport:
    rows = store.coverage()
    target_labels = {r.label for r in rows}
    pending: Counter[tuple[str, str]] = Counter()
    untargeted: Counter[str] = Counter()
    for log in logs:
        for d in log.decisions.values():
            if d.status == DecisionStatus.pending:
                pending[(d.label, Kind(d.kind).value)] += 1
            if d.status != DecisionStatus.rejected and d.label not in target_labels:
                untargeted[d.label] += 1
    lines = [
        CoverageLine(
            label=r.label,
            kind=Kind(r.kind),
            required=r.required,
            accepted=r.accepted,
            pending=pending[(r.label, Kind(r.kind).value)],
            met=r.met,
        )
        for r in rows
    ]
    lines.sort(key=lambda l: (l.met, l.label))
    return CoverageReport(
        lines=tuple(lines),
        untargeted=tuple(sorted(untargeted.items())),
        met_count=sum(l.met for l in lines),
        total=len(lines),
    )


def format_report(rep: CoverageReport) -> str:
    out = [f"coverage: {rep.met_count}/{rep.total} targets met"]
    for l in rep.lines:
        s = f"{l.label}: {l.accepted}/{l.required}"
        if l.met:
            s += " ✓"
        if l.pending > 0:
            s += f" (+{l.pending} pending)"
        out.append(s)
    if rep.untargeted:
        out.append("untargeted: " + ", ".join(f"{lab}×{n}" for lab, n in rep.untargeted))
    return "\n".join(out)

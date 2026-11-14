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
    untargeted: tuple[tuple[str, Kind, int], ...]  # (label, kind, count) of non-rejected decisions without a target
    met_count: int
    total: int


def report(store: GlyphStore, logs: Sequence[ReviewLog]) -> CoverageReport:
    rows = store.coverage()
    target_keys = {(r.label, Kind(r.kind)) for r in rows}
    pending: Counter[tuple[str, str]] = Counter()
    untargeted: Counter[tuple[str, Kind]] = Counter()
    for log in logs:
        for d in log.decisions.values():
            if d.status == DecisionStatus.pending:
                pending[(d.label, Kind(d.kind).value)] += 1
            if d.status != DecisionStatus.rejected and (d.label, Kind(d.kind)) not in target_keys:
                untargeted[(d.label, Kind(d.kind))] += 1
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
        untargeted=tuple((lab, kind, n) for (lab, kind), n in sorted(untargeted.items(), key=lambda kv: (kv[0][0], kv[0][1].value))),
        met_count=sum(l.met for l in lines),
        total=len(lines),
    )


def _untargeted_name(label: str, kind: Kind, target_labels: set[str]) -> str:
    """The label alone, or `label (kind)` when the kind is not the one its length implies or the label
    has a target of another kind (a kind mismatch)."""
    natural = Kind.single if len(label) == 1 else Kind.ligature
    return label if kind == natural and label not in target_labels else f"{label} ({kind.value})"


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
        targets = {l.label for l in rep.lines}
        names = (f"{_untargeted_name(lab, kind, targets)}×{n}" for lab, kind, n in rep.untargeted)
        out.append("untargeted: " + ", ".join(names))
    return "\n".join(out)

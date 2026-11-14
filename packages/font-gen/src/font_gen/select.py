"""Selection and ring assembly: eligible samples → rings, positional forms, ligature allowed sets, cmap, glyph order."""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from hwfont_schema import Kind, Quality

from font_gen.load import LoadedSample, LoadResult, Pos
from font_gen.naming import CMAP_ALIASES, POS_ORDER, base_name, glyph_name, label_key, validate
from font_gen.qa import Finding

RING_N = 7
POS_MIN = 3
RING_MIN = 2
RING_MAX = 13
POS_TIEBREAK: tuple[Pos, ...] = ("medi", "init", "fina", "isol")
PRIMES = {2, 3, 5, 7, 11, 13}

_ELIGIBLE_QUALITY = {Quality.good, Quality.marginal}

RingKey = tuple[str, Pos | None]


@dataclass(frozen=True)
class GlyphSpec:
    name: str
    sample_id: str | None
    label: str
    kind: Kind
    ring_key: RingKey
    slot: int
    source: str  # "strokes" | "raster" | "synthetic"


@dataclass(frozen=True)
class GlyphPlan:
    glyphs: tuple[GlyphSpec, ...]
    rings: dict[RingKey, tuple[str, ...]]
    ring_n: int
    transparent: frozenset[str]
    letters: frozenset[str]
    ligatures: dict[str, tuple[tuple[str, ...], frozenset[Pos]]]
    positional: dict[str, dict[Pos, str]]
    cmap: dict[int, str]
    eligible: dict[str, int]

    def names(self) -> list[str]:
        return [g.name for g in self.glyphs]

    def by_name(self) -> dict[str, GlyphSpec]:
        return {g.name: g for g in self.glyphs}


NOTDEF = GlyphSpec(".notdef", None, ".notdef", Kind.single, (".notdef", None), 0, "synthetic")
SPACE = GlyphSpec("space", None, "space", Kind.single, ("space", None), 0, "synthetic")


def is_eligible(ls: LoadedSample) -> bool:
    return ls.sample.quality in _ELIGIBLE_QUALITY and (ls.strokes is not None or ls.raster is not None)


def default_position(counts: dict[Pos, int], pos_min: int) -> Pos:
    """`medi` when it has `pos_min` samples, else the most-sampled position (ties medi, init, fina, isol)."""
    if counts.get("medi", 0) >= pos_min:
        return "medi"
    return max(POS_TIEBREAK, key=lambda p: (counts.get(p, 0), -POS_TIEBREAK.index(p)))


def _features(ls: LoadedSample) -> tuple[float, float, float]:
    m = ls.sample.metrics
    return (m.advance, m.bbox.w, m.bbox.h)


def medoid_index(samples: list[LoadedSample]) -> int:
    """Index of the sample minimising the summed median-normalised L1 distance to the others (ties by id)."""
    feats = [_features(ls) for ls in samples]
    medians = [statistics.median(f[k] for f in feats) or 1.0 for k in range(3)]
    norm = [tuple(f[k] / medians[k] for k in range(3)) for f in feats]
    totals = [sum(sum(abs(a - b) for a, b in zip(p, q)) for q in norm) for p in norm]
    return min(range(len(samples)), key=lambda i: (totals[i], samples[i].sample.id))


def ring_order(samples: list[LoadedSample]) -> list[LoadedSample]:
    """Stroke goods, stroke marginals, raster goods, raster marginals; the first non-empty group leads with its medoid."""
    groups: list[list[LoadedSample]] = [[], [], [], []]
    for ls in sorted(samples, key=lambda s: s.sample.id):
        groups[(0 if ls.strokes is not None else 2) + (0 if ls.sample.quality == Quality.good else 1)].append(ls)
    out: list[LoadedSample] = []
    for group in groups:
        if group and not out:
            group.insert(0, group.pop(medoid_index(group)))
        out.extend(group)
    return out


def sample_index(loaded: LoadResult) -> dict[str, LoadedSample]:
    return {ls.sample.id: ls for ls in loaded.samples}


def _ring_keys(label: str, by_pos: dict[Pos, list[LoadedSample]], pos_min: int) -> dict[RingKey, list[LoadedSample]]:
    every = [ls for p in POS_ORDER for ls in by_pos.get(p, [])]
    if not label.isalpha():
        return {(label, None): every}
    counts = {p: len(v) for p, v in by_pos.items()}
    default = default_position(counts, pos_min)
    rings: dict[RingKey, list[LoadedSample]] = {(label, None): []}
    for p in POS_ORDER:
        members = by_pos.get(p, [])
        if p == default or len(members) < pos_min:
            rings[(label, None)].extend(members)
        else:
            rings[(label, p)] = members
    return rings


def plan_glyphs(loaded: LoadResult, *, ring_n: int = RING_N, pos_min: int = POS_MIN) -> tuple[GlyphPlan, list[Finding]]:
    findings: list[Finding] = []
    by_label: dict[str, dict[Pos, list[LoadedSample]]] = {}
    for ls in loaded.samples:
        s = ls.sample
        if s.quality == Quality.bad:
            findings.append(Finding("info", "bad_sample_excluded", f"sample {s.id}: quality bad, excluded",
                                    label=s.label, sample_id=s.id))
            continue
        if is_eligible(ls):
            by_label.setdefault(s.label, {}).setdefault(ls.position, []).append(ls)

    rings: dict[RingKey, tuple[str, ...]] = {}
    specs: dict[RingKey, list[GlyphSpec]] = {}
    transparent: set[str] = {SPACE.name}
    letters: set[str] = set()
    positional: dict[str, dict[Pos, str]] = {}

    for label in sorted(by_label, key=label_key):
        kind = Kind.single if len(label) == 1 else Kind.ligature
        for key, members in _ring_keys(label, by_label[label], pos_min).items():
            _, pos = key
            ordered = ring_order(members)
            if len(ordered) > ring_n:
                dropped = [ls.sample.id for ls in ordered[ring_n:]]
                findings.append(Finding("info", "ring_surplus", f"ring {key}: {len(dropped)} surplus samples dropped",
                                        label=label, data={"ids": dropped}))
                ordered = ordered[:ring_n]
            ring_specs = [
                GlyphSpec(glyph_name(label, pos, slot), ls.sample.id, label, kind, key, slot,
                          "strokes" if ls.strokes is not None else "raster")
                for slot, ls in enumerate(ordered)
            ]
            names = tuple(g.name for g in ring_specs)
            rings[key] = names
            specs[key] = ring_specs
            n = len(names)
            if n == 1:
                transparent.add(names[0])
                findings.append(Finding("warn", "ring_singleton", f"ring {key} has one variant; {names[0]} will look stamped",
                                        label=label, glyph=names[0]))
            elif n < ring_n:
                findings.append(Finding("info", "ring_short", f"ring {key} has {n} of {ring_n} variants",
                                        label=label, data={"n": n}))
            goods = sum(ls.sample.quality == Quality.good for ls in ordered)
            if n >= 2 and goods < 2:
                findings.append(Finding("info", "marginal_used", f"ring {key} reaches {n} variants only via marginal samples",
                                        label=label))
            if label.isalpha() or label == "'":
                letters.update(names)
            if pos is not None:
                positional.setdefault(base_name(label), {})[pos] = names[0]

    ligatures: dict[str, tuple[tuple[str, ...], frozenset[Pos]]] = {}
    for label in sorted(by_label, key=label_key):
        if len(label) < 2:
            continue
        missing = [c for c in label if (c, None) not in rings]
        if missing:
            findings.append(Finding("warn", "ligature_component_missing",
                                    f"ligature {label!r}: no glyph for component(s) {''.join(missing)!r}; glyph emitted without a rule",
                                    label=label, data={"missing": missing}))
            continue
        allowed = frozenset(p for p, v in by_label[label].items() if v)
        ligatures[base_name(label)] = (tuple(base_name(c) for c in label), allowed)
        if len(allowed) < len(POS_ORDER):
            findings.append(Finding("info", "ligature_restricted", f"ligature {label!r} restricted to {sorted(allowed)}",
                                    label=label, data={"positions": sorted(allowed)}))

    eligible: dict[str, int] = {}
    accepted = {row.label: row.accepted for row in loaded.coverage}
    for t in loaded.targets:
        count = sum(len(v) for v in by_label.get(t.label, {}).values())
        eligible[t.label] = count
        if count == 0:
            findings.append(Finding("warn", "target_no_samples", f"target {t.label!r} has no eligible samples; no glyph",
                                    label=t.label))
        elif count < t.required_count:
            findings.append(Finding("info", "target_below_required",
                                    f"target {t.label!r}: {count} eligible of {t.required_count} required",
                                    label=t.label,
                                    data={"required": t.required_count, "accepted": accepted.get(t.label, 0), "eligible": count}))

    glyphs: list[GlyphSpec] = [NOTDEF, SPACE]
    singles = sorted((lab for lab in by_label if len(lab) == 1), key=label_key)
    clusters = sorted((lab for lab in by_label if len(lab) >= 2), key=label_key)
    for label in singles + clusters:
        for pos in (None, *POS_ORDER):
            glyphs.extend(specs.get((label, pos), []))

    cmap: dict[int, str] = {0x20: SPACE.name}
    for label in singles:
        cmap[ord(label)] = rings[(label, None)][0]
    present = {g.name for g in glyphs}
    for cp, target in CMAP_ALIASES.items():
        if target in present:
            cmap.setdefault(cp, target)

    plan = GlyphPlan(
        glyphs=tuple(glyphs), rings=rings, ring_n=ring_n, transparent=frozenset(transparent),
        letters=frozenset(letters), ligatures=ligatures, positional=positional, cmap=cmap, eligible=eligible,
    )
    findings.extend(validate(plan.names(), ring_n))
    if not rings:
        findings.append(Finding("error", "empty_font", "no ring could be built: the font would have no glyphs"))
    if ring_n not in PRIMES:
        findings.append(Finding("info", "ring_not_prime", f"ring size {ring_n} is not prime; repeated words may resonate",
                                data={"ring_n": ring_n}))
    return plan, findings

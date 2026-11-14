"""Metrics: hmtx, space, vertical metrics, ligature carets and per-sample metric findings."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from hwfont_schema import Kind

from font_gen.load import LoadedSample
from font_gen.naming import base_name
from font_gen.nib import NIB_EM, UPM
from font_gen.outline import Outline
from font_gen.qa import Finding
from font_gen.select import NOTDEF, SPACE, GlyphPlan, GlyphSpec

SPACE_FACTOR = 0.5
SPACE_CLAMP = (200, 400)
SPACE_DEFAULT = 300
ASC_FLOOR = 800
DESC_CEIL = -250
X_HEIGHT_DEFAULT = 450
CAP_HEIGHT_DEFAULT = 700
NOTDEF_METRICS = (500, 50)
ADVANCE_RANGE = (0.3, 3.0)
XH_TOL = 0.25
OVERHANG_EM = 0.15
XH_LETTERS = "acemnorsuvwxz"
CAP_LETTERS = "HITEFL"
# Descriptive table (spec §3): the single source for metrics_suspicious.
XH_RANGE = (0.35, 0.65)
CAP_RANGE = (550, 900)
INK_RATIO_RANGE = (0.3, 1.2)
ASC_TOP = 1250
DESC_BOTTOM = -450


def ceil10(v: float) -> int:
    return math.ceil(v / 10) * 10


def floor10(v: float) -> int:
    return math.floor(v / 10) * 10


@dataclass(frozen=True)
class VMetrics:
    ascender: int
    descender: int
    line_gap: int
    x_height: int
    cap_height: int


def _is_lower_single(g: GlyphSpec) -> bool:
    return g.sample_id is not None and len(g.label) == 1 and g.label.islower()


def _is_upper_single(g: GlyphSpec) -> bool:
    return g.sample_id is not None and len(g.label) == 1 and g.label.isupper()


def _is_ring_head(g: GlyphSpec) -> bool:
    """Slot 0 of the default (position-less) ring: the glyph the cmap or a ligature rule names."""
    return g.slot == 0 and g.ring_key[1] is None


def _sampled(plan: GlyphPlan) -> list[GlyphSpec]:
    return [g for g in plan.glyphs if g.sample_id is not None]


def hmetrics(
    plan: GlyphPlan, outlines: dict[str, Outline], samples: dict[str, LoadedSample], space_advance: int
) -> dict[str, tuple[int, int]]:
    """(advance, lsb) per glyph: advance from the store, lsb = outline xMin (0 for an empty outline)."""
    hm: dict[str, tuple[int, int]] = {}
    for g in plan.glyphs:
        if g.name == NOTDEF.name:
            hm[g.name] = NOTDEF_METRICS
        elif g.name == SPACE.name:
            hm[g.name] = (space_advance, 0)
        else:
            advance = round(samples[g.sample_id].sample.metrics.advance * UPM)
            outline = outlines.get(g.name)
            lsb = outline.bounds[0] if outline is not None and outline.bounds is not None else 0
            hm[g.name] = (advance, lsb)
    return hm


def lowercase_advances(plan: GlyphPlan, samples: dict[str, LoadedSample]) -> list[float]:
    return [samples[g.sample_id].sample.metrics.advance for g in plan.glyphs if _is_lower_single(g)]


def space_advance(lowercase_advances_em: Sequence[float], override_em: float | None = None) -> tuple[int, Finding | None]:
    if override_em is not None:
        return round(override_em * UPM), None
    if lowercase_advances_em:
        lo, hi = SPACE_CLAMP
        value = min(hi, max(lo, round(SPACE_FACTOR * statistics.median(lowercase_advances_em) * UPM)))
        basis = "half the median lowercase advance"
    else:
        value = SPACE_DEFAULT
        basis = "no lowercase samples; default"
    return value, Finding("info", "space_advance_assumed", f"space advance {value} units ({basis})", data={"advance": value})


def vertical_metrics(
    bounds: Sequence[tuple[int, int, int, int]],
    x_heights_em: Sequence[float],
    xh_ymax: Sequence[int],
    cap_ymax: Sequence[int],
    all_cap_ymax: Sequence[int],
) -> VMetrics:
    ascender = max(ASC_FLOOR, ceil10(max(b[3] for b in bounds))) if bounds else ASC_FLOOR
    descender = min(DESC_CEIL, floor10(min(b[1] for b in bounds))) if bounds else DESC_CEIL
    if x_heights_em:
        x_height = round(statistics.median(x_heights_em) * UPM)
    elif xh_ymax:
        x_height = round(statistics.median(xh_ymax))
    else:
        x_height = X_HEIGHT_DEFAULT
    if cap_ymax:
        cap_height = round(statistics.median(cap_ymax))
    elif all_cap_ymax:
        cap_height = round(statistics.median(all_cap_ymax))
    else:
        cap_height = CAP_HEIGHT_DEFAULT
    return VMetrics(ascender=ascender, descender=descender, line_gap=0, x_height=x_height, cap_height=cap_height)


def collect_vertical(plan: GlyphPlan, outlines: dict[str, Outline], samples: dict[str, LoadedSample]) -> VMetrics:
    bounds = [o.bounds for o in outlines.values() if o.bounds is not None]
    x_heights = [samples[g.sample_id].sample.metrics.x_height for g in plan.glyphs if _is_lower_single(g)]

    def head_ymax(letters: str | None) -> list[int]:
        out = []
        for g in plan.glyphs:
            if not _is_ring_head(g) or g.sample_id is None or len(g.label) != 1:
                continue
            if (g.label in letters) if letters is not None else g.label.isupper():
                outline = outlines.get(g.name)
                if outline is not None and outline.bounds is not None:
                    out.append(outline.bounds[3])
        return out

    return vertical_metrics(bounds, x_heights, head_ymax(XH_LETTERS), head_ymax(CAP_LETTERS), head_ymax(None))


def ligature_carets(plan: GlyphPlan, hm: dict[str, tuple[int, int]]) -> dict[str, tuple[int, ...]]:
    """Caret positions per ligature base at component boundaries, proportional to component advances (else even)."""
    bases: dict[str, tuple[str, ...]] = {name: comps for name, (comps, _) in plan.ligatures.items()}
    for g in plan.glyphs:
        if g.kind == Kind.ligature and _is_ring_head(g) and g.name not in bases:
            bases[g.name] = tuple(base_name(c) for c in g.label)
    carets: dict[str, tuple[int, ...]] = {}
    for name, comps in bases.items():
        if name not in hm:
            continue
        lig_adv = hm[name][0]
        widths = [hm[c][0] if c in hm else None for c in comps]
        if None in widths or sum(widths) <= 0:
            widths = [1] * len(comps)
        total = sum(widths)
        cum = 0
        positions = []
        for w in widths[:-1]:
            cum += w
            positions.append(round(lig_adv * cum / total))
        carets[name] = tuple(positions)
    return carets


def metric_findings(
    plan: GlyphPlan, outlines: dict[str, Outline], samples: dict[str, LoadedSample], nib_em: float = NIB_EM
) -> list[Finding]:
    sampled = _sampled(plan)
    if not sampled:
        return []
    by_kind: dict[Kind, list[float]] = {}
    for g in sampled:
        by_kind.setdefault(g.kind, []).append(samples[g.sample_id].sample.metrics.advance)
    median_adv = {k: statistics.median(v) for k, v in by_kind.items()}
    median_xh = statistics.median(samples[g.sample_id].sample.metrics.x_height for g in sampled)

    findings: list[Finding] = []
    for g in sampled:
        m = samples[g.sample_id].sample.metrics
        ctx = {"label": g.label, "sample_id": g.sample_id, "glyph": g.name}

        def suspicious(message: str, **data) -> None:
            findings.append(Finding("warn", "metrics_suspicious", message, data=data, **ctx))

        lo, hi = ADVANCE_RANGE
        med = median_adv[g.kind]
        if not lo * med <= m.advance <= hi * med:
            findings.append(Finding("warn", "advance_outlier",
                                    f"advance {m.advance:.3f} em outside [{lo}, {hi}] x the {g.kind.value} median {med:.3f}",
                                    data={"advance": m.advance, "median": med}, **ctx))
        if median_xh > 0 and abs(m.x_height - median_xh) / median_xh > XH_TOL:
            findings.append(Finding("warn", "x_height_outlier",
                                    f"x-height {m.x_height:.3f} em deviates more than {XH_TOL:.0%} from the median {median_xh:.3f}",
                                    data={"x_height": m.x_height, "median": median_xh}, **ctx))
        if not XH_RANGE[0] <= m.x_height <= XH_RANGE[1]:
            suspicious(f"x-height {m.x_height:.3f} em outside {XH_RANGE}", x_height=m.x_height)

        outline = outlines.get(g.name)
        if outline is None or outline.bounds is None:
            continue
        x0, y0, x1, y1 = outline.bounds
        ratio = (m.bbox.w + nib_em) / m.advance
        if not INK_RATIO_RANGE[0] <= ratio <= INK_RATIO_RANGE[1]:
            suspicious(f"ink-width/advance ratio {ratio:.2f} outside {INK_RATIO_RANGE}", ratio=ratio)
        limit = (m.advance + OVERHANG_EM) * UPM
        if x1 > limit:
            suspicious(f"ink xMax {x1} overhangs advance + {OVERHANG_EM} em ({limit:.0f})", x_max=x1, limit=limit)
        if y1 > ASC_TOP or y0 < DESC_BOTTOM:
            suspicious(f"ink y-range [{y0}, {y1}] outside [{DESC_BOTTOM}, {ASC_TOP}]", y_min=y0, y_max=y1)
        if _is_upper_single(g) and _is_ring_head(g) and not CAP_RANGE[0] <= y1 <= CAP_RANGE[1]:
            suspicious(f"cap height {y1} outside {CAP_RANGE}", cap_height=y1)
    return findings

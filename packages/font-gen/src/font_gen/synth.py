"""Deterministic synthetic glyph stores: test fixtures, `font-gen demo`, and the reference implementation of em-v1.

Strokes come from a small parametric alphabet in em (y-up, baseline 0, x-height 0.45), are jittered per variant
with a seeded RNG, translated so the ink starts at a seeded LSB, and written through `GlyphStore.add_sample`
together with a padded raster rendered at `RASTER_SCALE` px/em.
"""

from __future__ import annotations

import io
import math
import random
import string
from pathlib import Path

from hwfont_schema import (
    BBox,
    Context,
    Contour,
    GlyphStore,
    Kind,
    Metrics,
    PositionInWord,
    Quality,
    ReviewStatus,
    Sample,
    StrokeData,
    StrokePoint,
    Target,
)
from hwfont_schema.emspace import RASTER_PAD, raster_em_to_px
from PIL import Image, ImageDraw

SESSION_X_HEIGHT = 0.45
RASTER_SCALE = 256
NIB_EM = 0.045
POSITIONS = ("init", "medi", "fina", "isol")

CAP_HEIGHT = 0.70
DESCENDER = -0.20
STEM_DRIFT = 0.08
DOT_STUB = 0.003
LIGATURE_GAP = 0.05
SIDE_BEARING = (0.05, 0.08)
JITTER_SCALE = 0.03
JITTER_SHEAR_DEG = 2.0
JITTER_BOUNCE = 0.01
PRESSURE_RAMP = (0.3, 0.9)
WIDTH_SWELL = 0.015
DEGENERATE_POINT = (0.1, 0.1)
DEGENERATE_EXTENT = 0.001
DEGENERATE_ADVANCE = 0.5
NEIGHBOUR = "a"
CREATED_AT = "1970-01-01T00:00:00Z"

_POSITION_IN_WORD = {
    "init": PositionInWord.initial,
    "medi": PositionInWord.medial,
    "fina": PositionInWord.final,
    "isol": PositionInWord.isolated,
}
_NEIGHBOURS: dict[str, tuple[str | None, str | None]] = {
    "init": (None, NEIGHBOUR),
    "medi": (NEIGHBOUR, NEIGHBOUR),
    "fina": (NEIGHBOUR, None),
    "isol": (None, None),
}

# --- target catalog: a verbatim copy of capture_template.targets (copied, not imported) -------------------------

DEFAULT_GLYPH_COUNT = 12
DEFAULT_LIGATURE_COUNT = 8
DEFAULT_GLYPHS = string.ascii_letters + string.digits + ".,!?'\"-;:()"
DEFAULT_LIGATURES = [
    # existing
    "fi", "fl", "ff", "ffi", "ffl", "eft", "fore", "ough", "tion", "ing",
    # common ligatures + digraphs (count 8)
    "st", "ll", "ss", "ee", "oo", "th", "ch", "sh", "wh", "ck", "ng", "qu", "gh", "ea", "ou",
    # rare lowercase clusters (count 4)
    "ct", "sp", "ph", "oft", "tch", "dge", "igh", "ght", "thr", "str", "nth",
    # common capital digraphs (count 6)
    "Th", "Sh", "Wh", "St",
    # rare capital clusters (count 3)
    "Ch", "Ph", "Sp", "Qu", "Thr", "Str", "Sch", "Shr",
]

# Per-label required counts for clusters that differ from DEFAULT_LIGATURE_COUNT (8).
DEFAULT_CLUSTER_COUNTS: dict[str, int] = {
    **{lab: 4 for lab in ["ct", "sp", "ph", "oft", "tch", "dge", "igh", "ght", "thr", "str", "nth"]},
    **{lab: 6 for lab in ["Th", "Sh", "Wh", "St"]},
    **{lab: 3 for lab in ["Ch", "Ph", "Sp", "Qu", "Thr", "Str", "Sch", "Shr"]},
}


def _build(
    glyph_chars: str,
    glyph_count: int,
    ligatures: list[str],
    ligature_count: int,
    overrides: dict[str, int],
) -> list[Target]:
    seen: set[str] = set()
    targets: list[Target] = []

    for ch in glyph_chars:
        if ch in seen:
            raise ValueError(f"duplicate target label: {ch!r}")
        seen.add(ch)
        targets.append(
            Target(label=ch, kind=Kind.single, required_count=overrides.get(ch, glyph_count))
        )

    for lig in ligatures:
        if len(lig) < 2:
            raise ValueError(f"ligature must be at least 2 characters: {lig!r}")
        if lig in seen:
            raise ValueError(f"duplicate target label: {lig!r}")
        seen.add(lig)
        targets.append(
            Target(
                label=lig, kind=Kind.ligature, required_count=overrides.get(lig, ligature_count)
            )
        )

    return targets


def default_targets() -> list[Target]:
    return _build(
        DEFAULT_GLYPHS,
        DEFAULT_GLYPH_COUNT,
        DEFAULT_LIGATURES,
        DEFAULT_LIGATURE_COUNT,
        DEFAULT_CLUSTER_COUNTS,
    )


def demo_spec() -> dict[str, dict[str, int]]:
    """Per-position sample counts for every default target (what `font-gen demo` builds)."""
    spec: dict[str, dict[str, int]] = {}
    for t in default_targets():
        if len(t.label) > 1:
            spec[t.label] = {"init": 1, "medi": 2, "fina": 2, "isol": 1}
        elif t.label.isalpha():
            spec[t.label] = {"medi": 4, "init": 3, "fina": 3}
        else:
            spec[t.label] = {"isol": 3}
    return spec


# --- parametric alphabet (em, y-up, baseline 0) --------------------------------------------------------------------

Polyline = list[tuple[float, float]]


def _stem(x: float, y0: float, y1: float, n: int = 12) -> Polyline:
    """Pen line from y0 to y1, drifting STEM_DRIFT em right from bottom to top so its bbox is never zero-wide."""
    lo = min(y0, y1)
    span = abs(y1 - y0)
    pts = []
    for i in range(n):
        y = y0 + (y1 - y0) * i / (n - 1)
        pts.append((x - STEM_DRIFT / 2 + STEM_DRIFT * (y - lo) / span, y))
    return pts


def _loop(cx: float, cy: float, rx: float, ry: float, n: int = 32) -> Polyline:
    pts = [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return pts + [pts[0]]


def _bar(x0: float, x1: float, y: float, n: int = 6) -> Polyline:
    return [(x0 + (x1 - x0) * i / (n - 1), y) for i in range(n)]


def _dot(x: float, y: float) -> Polyline:
    return [(x, y), (x + DOT_STUB, y + DOT_STUB)]


def _s_curve(y_scale: float = 1.0) -> Polyline:
    return [
        (0.19 + 0.15 * math.cos(math.pi * k / 11 * 2), SESSION_X_HEIGHT * k / 11 * y_scale) for k in range(12)
    ]


def _lissajous(label: str, cx: float, cy: float, rx: float, ry: float) -> Polyline:
    s = sum(map(ord, label))
    p, q = 1 + s % 3, 1 + s % 2
    ts = [2 * math.pi * i / 39 for i in range(40)]
    return [(cx + rx * math.cos(p * t), cy + ry * math.sin(q * t)) for t in ts]


def _generic(label: str) -> list[Polyline]:
    if label == ".":
        return [_dot(0.06, 0.02)]
    if label == ",":
        return [[(0.08, 0.06), (0.07, -0.02), (0.03, -0.10)]]
    if not label.isalnum():
        return [_lissajous(label, 0.11, 0.35, 0.09, 0.11)]
    shape = [_lissajous(label, 0.22, 0.225, 0.18, 0.22)]
    if label.isupper() or label.isdigit():
        shape.append(_stem(0.42, 0.0, CAP_HEIGHT))
    elif label in "jpqy":
        shape.append(_stem(0.42, SESSION_X_HEIGHT, DESCENDER))
    return shape


def _bowl() -> Polyline:
    return _loop(0.20, 0.225, 0.18, 0.22)


_SHAPES = {
    "l": lambda: [_stem(0.10, 0.0, 0.70)],
    "o": lambda: [_loop(0.25, 0.225, 0.20, 0.225)],
    "a": lambda: [_bowl(), _stem(0.40, 0.45, 0.0)],
    "e": lambda: [_bowl(), _bar(0.04, 0.36, 0.25)],
    "t": lambda: [_stem(0.15, 0.0, 0.62), _bar(0.02, 0.30, 0.42)],
    "f": lambda: [_stem(0.18, 0.0, 0.70), _bar(0.04, 0.34, 0.42)],
    "i": lambda: [_stem(0.10, 0.0, 0.45), _dot(0.12, 0.60)],
    "n": lambda: [_stem(0.08, 0.0, 0.45), [(0.08, 0.35), (0.25, 0.47), (0.42, 0.0)]],
    "g": lambda: [_bowl(), _stem(0.40, 0.45, DESCENDER)],
    "s": lambda: [_s_curve()],
    "r": lambda: [_stem(0.08, 0.0, 0.45), [(0.08, 0.35), (0.22, 0.45), (0.34, 0.40)]],
    "T": lambda: [_bar(0.02, 0.50, 0.70), _stem(0.26, 0.70, 0.0)],
    "S": lambda: [_s_curve(CAP_HEIGHT / SESSION_X_HEIGHT)],
}


def _char_shape(ch: str) -> list[Polyline]:
    return _SHAPES[ch]() if ch in _SHAPES else _generic(ch)


def _label_shape(label: str) -> list[Polyline]:
    """Glyph polylines; a cluster sets its components side by side, LIGATURE_GAP em apart."""
    if len(label) == 1:
        return _char_shape(label)
    out: list[Polyline] = []
    cursor = 0.0
    for ch in label:
        comp = _char_shape(ch)
        xmin = min(x for pl in comp for x, _ in pl)
        xmax = max(x for pl in comp for x, _ in pl)
        out.extend([(x - xmin + cursor, y) for x, y in pl] for pl in comp)
        cursor += xmax - xmin + LIGATURE_GAP
    return out


# --- strokes, samples, rasters -------------------------------------------------------------------------------------


def _fractions(pl: Polyline) -> list[float]:
    """Arc-length fraction of each vertex (index fraction for a zero-length polyline)."""
    cum = [0.0]
    for (x0, y0), (x1, y1) in zip(pl, pl[1:]):
        cum.append(cum[-1] + math.hypot(x1 - x0, y1 - y0))
    if cum[-1] == 0:
        return [i / (len(pl) - 1) for i in range(len(pl))]
    return [c / cum[-1] for c in cum]


def _to_contour(pl: Polyline, pressure: bool, width: bool) -> Contour:
    points = []
    for (x, y), s in zip(pl, _fractions(pl)):
        points.append(
            StrokePoint(
                x=x,
                y=y,
                pressure=PRESSURE_RAMP[0] + (PRESSURE_RAMP[1] - PRESSURE_RAMP[0]) * s if pressure else None,
                width=NIB_EM + WIDTH_SWELL * math.sin(math.pi * s) if width else None,
            )
        )
    return Contour(points=points)


def _rng(label: str, pos: str, k: int, seed: int) -> random.Random:
    return random.Random(f"{seed}:{label}:{pos}:{k}")


def synth_strokes(
    label: str,
    pos: str,
    k: int,
    seed: int,
    *,
    pressure: bool = False,
    width: bool = False,
    degenerate: bool = False,
) -> StrokeData:
    """Jittered em-v1 strokes for variant `k`; the ink's left edge sits at a seeded LSB in [0.05, 0.08]."""
    if degenerate:
        pl = [DEGENERATE_POINT, DEGENERATE_POINT]
        return StrokeData(contours=[_to_contour(pl, pressure, width)])
    rng = _rng(label, pos, k, seed)
    scale = 1 + JITTER_SCALE * rng.uniform(-1, 1)
    shear = math.tan(math.radians(JITTER_SHEAR_DEG * rng.uniform(-1, 1)))
    bounce = JITTER_BOUNCE * rng.uniform(-1, 1)
    lsb = rng.uniform(*SIDE_BEARING)
    shape = [[(x * scale + y * scale * shear, y * scale + bounce) for x, y in pl] for pl in _label_shape(label)]
    dx = lsb - min(x for pl in shape for x, _ in pl)
    return StrokeData(contours=[_to_contour([(x + dx, y) for x, y in pl], pressure, width) for pl in shape])


def _tight_bbox(strokes: StrokeData) -> BBox:
    xs = [p.x for c in strokes.contours for p in c.points]
    ys = [p.y for c in strokes.contours for p in c.points]
    return BBox(x=min(xs), y=min(ys), w=max(xs) - min(xs), h=max(ys) - min(ys))


def render_raster(strokes: StrokeData, bbox: BBox, scale: int = RASTER_SCALE) -> bytes:
    """Grayscale PNG of the strokes, dark on white, covering `bbox` padded by RASTER_PAD at `scale` px/em."""
    size = (round((bbox.w + 2 * RASTER_PAD) * scale), round((bbox.h + 2 * RASTER_PAD) * scale))
    img = Image.new("L", size, 255)
    draw = ImageDraw.Draw(img)
    nib_px = round(NIB_EM * scale)
    for c in strokes.contours:
        draw.line([raster_em_to_px(p.x, p.y, bbox, scale) for p in c.points], fill=0, width=nib_px, joint="curve")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def synth_sample(
    label: str,
    pos: str,
    k: int,
    seed: int,
    *,
    pressure: bool = False,
    width: bool = False,
    degenerate: bool = False,
) -> tuple[Sample, StrokeData, bytes]:
    """One synthetic sample with id `f"{pos}-{k:02d}"` (synth_store prefixes the label index)."""
    strokes = synth_strokes(label, pos, k, seed, pressure=pressure, width=width, degenerate=degenerate)
    if degenerate:
        x, y = DEGENERATE_POINT
        bbox = BBox(x=x, y=y, w=DEGENERATE_EXTENT, h=DEGENERATE_EXTENT)
        advance = DEGENERATE_ADVANCE
    else:
        bbox = _tight_bbox(strokes)
        rsb = random.Random(f"{seed}:{label}:{pos}:{k}:rsb").uniform(*SIDE_BEARING)
        advance = bbox.x + bbox.w + rsb
    left, right = _NEIGHBOURS[pos]
    sample = Sample(
        id=f"{pos}-{k:02d}",
        label=label,
        kind=Kind.single if len(label) == 1 else Kind.ligature,
        context=Context(
            source_word=(left or "") + label + (right or ""),
            left_neighbor=left,
            right_neighbor=right,
            position_in_word=_POSITION_IN_WORD[pos],
        ),
        metrics=Metrics(baseline=0.0, x_height=SESSION_X_HEIGHT, advance=advance, bbox=bbox),
        quality=Quality.good,
        review_status=ReviewStatus.accepted,
        capture_session_id=f"synth-{seed}",
        created_at=CREATED_AT,
    )
    return sample, strokes, render_raster(strokes, bbox)


def _store_targets(labels: set[str]) -> list[Target]:
    defaults = {t.label: t for t in default_targets()}
    out = []
    for label in sorted(labels):
        if label in defaults:
            out.append(defaults[label])
        elif len(label) == 1:
            out.append(Target(label=label, kind=Kind.single, required_count=DEFAULT_GLYPH_COUNT))
        else:
            out.append(Target(label=label, kind=Kind.ligature, required_count=DEFAULT_LIGATURE_COUNT))
    return out


def synth_store(
    root: str | Path,
    spec: dict[str, dict[str, int]],
    seed: int = 1,
    *,
    raster_only: frozenset[str] = frozenset(),
    bad: dict[str, int] | None = None,
    marginal: dict[str, int] | None = None,
    missing_targets: frozenset[str] = frozenset(),
    degenerate: frozenset[str] = frozenset(),
    kind_mismatch: frozenset[str] = frozenset(),
    pressure: bool = False,
    widths: frozenset[str] = frozenset(),
    rasters: bool = True,
    targets: list[Target] | None = None,
) -> GlyphStore:
    """Build a GlyphStore at `root` from `spec` ({label: {pos: count}}); the returned store is open."""
    bad = bad or {}
    marginal = marginal or {}
    store = GlyphStore.create(root)
    for target in sorted(targets if targets is not None else _store_targets(set(spec) | missing_targets),
                         key=lambda t: t.label):
        store.add_target(target)
    for label_index, label in enumerate(sorted(spec)):
        rows = []
        for pos in POSITIONS:
            for k in range(spec[label].get(pos, 0)):
                sample, strokes, raster = synth_sample(
                    label, pos, k, seed, pressure=pressure, width=label in widths, degenerate=label in degenerate
                )
                rows.append((sample.model_copy(update={"id": f"{label_index:03d}-{sample.id}"}), strokes, raster))
        by_id = sorted(s.id for s, _, _ in rows)
        n_bad, n_marginal = bad.get(label, 0), marginal.get(label, 0)
        bad_ids = set(by_id[len(by_id) - n_bad:]) if n_bad else set()
        marginal_ids = set(by_id[len(by_id) - n_marginal:]) if n_marginal else set()
        for sample, strokes, raster in rows:
            update: dict[str, object] = {}
            if sample.id in bad_ids:
                update["quality"] = Quality.bad
            elif sample.id in marginal_ids:
                update["quality"] = Quality.marginal
            if label in kind_mismatch:
                update["kind"] = Kind.ligature if sample.kind is Kind.single else Kind.single
            if update:
                sample = sample.model_copy(update=update)
            store.add_sample(
                sample,
                strokes=None if label in raster_only else strokes,
                raster=raster if rasters or label in raster_only else None,
            )
    return store

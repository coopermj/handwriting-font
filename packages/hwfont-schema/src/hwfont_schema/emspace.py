r"""em-v1: the em-space convention (Contract Y) shared by review and font-gen.

### Frame and unit

- **Cartesian, x to the right, y UP** (font convention; page pixels are y-down, so `review` flips). The schema test values `Metrics(baseline=0.0, x_height=0.5, advance=0.6, bbox=BBox(x=0, y=0, w=0.5, h=0.5))` are only consistent with y-up.
- **`1.0` = one em = the ruled row height of the capture region the sample was written in** (`Region.bbox.h` of Contract X, in page pixels; 70 px = 7.87 mm at 226 dpi on the default template, `PageConfig.line_height_px`). Scale `s = 1 / Region.bbox.h` is applied **uniformly to x and y** and is one constant for every sample of a template layout. It is a printed, physical quantity, derivable from Contract X without statistics; it keeps size relations between an `l` and an `a` as data (never per-sample "fit to box"); and it lands handwriting at the proportions in the **descriptive table** below (the one source of truth for "sane"). Row pitch (150 px) is rejected: x-height would be ≈ 0.25 em and 12 pt text would look like 6 pt.
- **Forbidden:** any per-sample scaling, rotation or normalisation. The only transforms between page px and em are the page's fiducial affine (already applied by ingest) and `s`.
- `font-gen` uses **UPM = 1000**; 1 font unit = 0.001 em.

### Origin

- `y = 0` is the row's ruled baseline (`Region.baseline_y`): `y_em = (baseline_y_px − y_px) · s`.
- `x = 0` is the sample's **glyph origin** defined by the half-gap rule: `x_em = (x_px − origin_x_px) · s` with `origin_x_px = ink.xmin_px − LSB_px` (below).

### The half-gap rule (sidebearings and advance)

Let `ink` be the sample's **centerline extent** in x (the bbox of its stroke points; for a raster-only sample, of its ink pixels; a cluster is one ink). Measured on the page in the sample's source word:

- `LSB = ½ · (ink.xmin − left_neighbour.ink.xmax)`, `RSB = ½ · (right_neighbour.ink.xmin − ink.xmax)`.
- Missing neighbour (word edge, `left_neighbor`/`right_neighbor` is `None`): the session median of the measured half-gaps (fallback `0.05` em).
- **Negative half-gaps are allowed and kept** — neighbours whose ink overlaps are nuance, not noise.
- `bbox.x = LSB`, `advance = LSB + bbox.w + RSB` (`> 0` is schema-enforced; if the arithmetic yields `≤ 0`, review clamps to `0.02` and flags the sample `marginal`).

Because advances are built from half-gaps, consecutive advances are additive: the font reproduces the page's origin-to-origin spacing exactly, independent of the nib model.

### Field meanings

| Field | Meaning in em-v1 |
|---|---|
| `Metrics.baseline` | y of this sample's baseline in its own frame: `0.0` under standard normalisation. A nonzero value is permitted (baseline bounce the reviewer chose to keep); `font-gen` translates the ink **and the stored bbox** by `−baseline` and flags `abs(baseline) > 0.1`. |
| `Metrics.x_height` | the **session** x-height in em above the baseline (`> 0`): median over the writer's x-height letters `a c e m n o r s u v w x z` of the 90th-percentile top of their centerlines. The same value is written on every sample of a session (a `T` carries it too). `font-gen` reads it only in aggregate. |
| `Metrics.advance` | distance from this origin to the next unit's origin in the source word (`> 0`), per the half-gap rule. Ink may overhang the advance (negative RSB); overhang beyond `0.15` em is flagged. |
| `Metrics.bbox` | tight box of the **centerline points** (stroke-bearing sample) or of the **ink pixels** (raster-only sample); `(x, y)` is the bottom-left corner (y-up), so `bbox.x = LSB` and `bbox.y < 0` for descenders. Visible ink exceeds it by ≈ nib/2 on every side. |
| `StrokeData.contours` | pen centerlines in writing order, one `Contour` per pen-down…pen-up, `points` in pen direction where known. Unsmoothed (`font-gen` smooths; `review` must not). `pressure ∈ [0, 1]` (1 = device maximum) or `None` for every point of the sample; mixed within a sample is a QA flag (interpolated). Optional `width` = local full ink width in em. Adjacent duplicate points allowed; a dot may be a 2-point stub. |
| `raster/<id>.png` | 8-bit grayscale (RGB tolerated), dark ink on light, covering `Metrics.bbox` **padded by `RASTER_PAD = 0.05` em on every side**: pixel `(0, 0)` (top-left) ↔ `(bbox.x − 0.05, bbox.y + bbox.h + 0.05)`; scale `S = width_px / (bbox.w + 0.10) = height_px / (bbox.h + 0.10)` px/em (recommended `S = 256`). `font-gen` infers `S` from the image size; the two scales must agree within 2 %. The pad is what keeps the nib's overhang inside the crop. |
| `Sample.kind` | must agree with the label: `single` ⇔ `len(label) == 1`, `ligature` ⇔ `len(label) ≥ 2`, and with the `Target` of the same label. `font-gen` keys everything on the label and only *checks* `kind` (`kind_mismatch`, §5). |

### Invariants (checked by `check_sample_em`; a violation is a QA flag, the sample is still used when usable)

- every stroke point: `−0.5 ≤ x ≤ 2.5`, `−0.6 ≤ y ≤ 1.4`; `0 < advance ≤ 2.5`; `0 < x_height < 1`; `−0.1 ≤ baseline ≤ 0.1`;
- stroke bbox equals `Metrics.bbox` within `0.005` em on every edge (stroke-bearing samples);
- raster, when a size is supplied: `width_px / (bbox.w + 2·RASTER_PAD)` and `height_px / (bbox.h + 2·RASTER_PAD)` agree within 2 %, and both are `≥ 32` px/em;
- pressure all-`None` or all-float within a sample; `width`, when present, in `(0, 0.2]`.

**Descriptive table** (the single source for `metrics_suspicious`, §7.5 and §8; not invariants): x-height `0.35–0.65` em, cap height `0.55–0.90`, ascender top `≤ 1.25`, descender bottom `≥ −0.45`, ink-width/advance ratio `0.3–1.2`, outline bounds within `[−600, 2500] × [−700, 1400]` units.
"""

from __future__ import annotations

import struct

from hwfont_schema.sample import BBox, Sample
from hwfont_schema.strokes import StrokeData

EM_CONVENTION_VERSION = 1
RASTER_PAD = 0.05
POINT_X = (-0.5, 2.5)
POINT_Y = (-0.6, 1.4)
ADVANCE_MAX = 2.5
BASELINE_ABS = 0.1
BBOX_TOL = 0.005
WIDTH_MAX = 0.2
RASTER_MIN_PX_PER_EM = 32
RASTER_SCALE_TOL = 0.02
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_size(data: bytes) -> tuple[int, int]:
    """(width, height) from the IHDR chunk; ValueError when the PNG signature is missing."""
    if data[:8] != _PNG_SIGNATURE or len(data) < 24:
        raise ValueError("not a PNG: missing signature or truncated IHDR")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def raster_px_to_em(col: float, row: float, bbox: BBox, scale: float) -> tuple[float, float]:
    """Raster pixel (0, 0 = top-left, y down) to em space (y up)."""
    return bbox.x - RASTER_PAD + col / scale, bbox.y + bbox.h + RASTER_PAD - row / scale


def raster_em_to_px(x: float, y: float, bbox: BBox, scale: float) -> tuple[float, float]:
    """Em space (y up) to raster pixel (col, row); inverse of raster_px_to_em."""
    return (x - bbox.x + RASTER_PAD) * scale, (bbox.y + bbox.h + RASTER_PAD - y) * scale


def _check_raster(bbox: BBox, raster: bytes | tuple[int, int]) -> list[str]:
    w, h = png_size(raster) if isinstance(raster, bytes) else raster
    sx = w / (bbox.w + 2 * RASTER_PAD)
    sy = h / (bbox.h + 2 * RASTER_PAD)
    out: list[str] = []
    if abs(sx - sy) / max(sx, sy) > RASTER_SCALE_TOL:
        out.append(f"raster_scale_mismatch: x scale {sx:.1f} vs y scale {sy:.1f} px/em (tol {RASTER_SCALE_TOL:.0%})")
    if min(sx, sy) < RASTER_MIN_PX_PER_EM:
        out.append(f"raster_resolution: {min(sx, sy):.1f} px/em < {RASTER_MIN_PX_PER_EM}")
    return out


def check_sample_em(
    sample: Sample,
    strokes: StrokeData | None,
    raster: bytes | tuple[int, int] | None = None,
) -> list[str]:
    """Return em-v1 violations as '<code>: <detail>' strings; [] when conforming."""
    m = sample.metrics
    out: list[str] = []
    if m.advance > ADVANCE_MAX:
        out.append(f"advance_range: advance {m.advance} > {ADVANCE_MAX}")
    if not 0 < m.x_height < 1:
        out.append(f"x_height_range: x_height {m.x_height} not in (0, 1)")
    if abs(m.baseline) > BASELINE_ABS:
        out.append(f"baseline_range: baseline {m.baseline} outside [-{BASELINE_ABS}, {BASELINE_ABS}]")
    if strokes is None:
        if raster is not None:
            out.extend(_check_raster(m.bbox, raster))
        return out

    points = [p for c in strokes.contours for p in c.points]
    for p in points:
        if not (POINT_X[0] <= p.x <= POINT_X[1] and POINT_Y[0] <= p.y <= POINT_Y[1]):
            out.append(f"point_range: point ({p.x}, {p.y}) outside x {POINT_X}, y {POINT_Y}")
    edges = (
        ("xmin", min(p.x for p in points), m.bbox.x),
        ("ymin", min(p.y for p in points), m.bbox.y),
        ("xmax", max(p.x for p in points), m.bbox.x + m.bbox.w),
        ("ymax", max(p.y for p in points), m.bbox.y + m.bbox.h),
    )
    for name, actual, expected in edges:
        if abs(actual - expected) > BBOX_TOL:
            out.append(f"bbox_mismatch: {name} strokes {actual:.4f} vs bbox {expected:.4f} (tol {BBOX_TOL})")
    n_none = sum(p.pressure is None for p in points)
    if 0 < n_none < len(points):
        out.append(f"pressure_mixed: {n_none} of {len(points)} points have no pressure")
    for p in points:
        if p.width is not None and not (0 < p.width <= WIDTH_MAX):
            out.append(f"width_range: width {p.width:.4f} outside (0, {WIDTH_MAX}] em")
    if raster is not None:
        out.extend(_check_raster(m.bbox, raster))
    return out

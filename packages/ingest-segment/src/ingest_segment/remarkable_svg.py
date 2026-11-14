"""reMarkable SVG export adapter.

A reMarkable SVG export of a written-on capture page carries two layers:

* an embedded full-page raster (`<image>`) holding the printed template (prompts,
  rules, the corner fiducials), and
* one filled `<path>` per pen stroke: the *outline* of the ink, in a viewBox whose
  x-origin is usually centered (negative x on the left half of the page).

This module turns that into what the rest of the pipeline consumes: a page raster
(template + real ink) and a `PenStroke` per path carrying the ink outline, one or
more centerline polylines with the measured ink width at each point, and a coverage
score saying how much of the skeleton those polylines account for.
"""

from __future__ import annotations

import base64
import io
import math
import re
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from skimage.morphology import skeletonize

Point = tuple[float, float]
Pixel = tuple[int, int]  # (row, col)
# a viewBox tuple is (min_x, min_y, width, height)
ViewBox = tuple[float, float, float, float]

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
# <path> elements under these containers are definitions, not drawn ink
_NON_INK_CONTAINERS = {"defs", "clipPath", "mask", "symbol", "pattern", "marker"}

_MASK_PAD = 3            # blank border around a path's mask so edge ink isn't clipped by skeletonize
_MIN_SKELETON_PX = 4     # skeletons smaller than this collapse to a centroid stub
_MIN_BRANCH_PX = 5       # skeleton branches shorter than this are spurs, not ink
_RDP_EPSILON = 1.0       # Douglas-Peucker simplification tolerance, in pixels
_COLLINEAR_EPS = 0.5     # px; a ring whose points all lie within this of one line has no area
_BEZIER_SAMPLES = 8      # flattening resolution for curve commands (the export uses none)
LOW_COVERAGE = 0.9       # a stroke whose polylines cover less of its skeleton than this is flagged


# --------------------------------------------------------------------------- parsing


def _local(tag: str) -> str:
    """Strip an XML namespace, e.g. '{http://...}path' -> 'path'."""
    return tag.rsplit("}", 1)[-1]


_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_TOKEN = re.compile(rf"[MmLlHhVvZzCcSsQqTtAa]|{_NUMBER}")
_LENGTH = re.compile(rf"^\s*({_NUMBER})\s*([a-zA-Z%]*)\s*$")
_CSS_PX_PER_UNIT = {"": 1.0, "px": 1.0, "pt": 96 / 72, "pc": 16.0, "in": 96.0, "mm": 96 / 25.4, "cm": 96 / 2.54}


def _parse_length(value: str) -> float:
    """Parse an SVG length ('1410', '1410px', '210mm') into user units (CSS px)."""
    m = _LENGTH.match(value)
    if not m:
        raise ValueError(f"cannot parse SVG length {value!r}")
    unit = m.group(2).lower()
    if unit not in _CSS_PX_PER_UNIT:
        raise ValueError(f"unsupported SVG length unit {unit!r} in {value!r}")
    return float(m.group(1)) * _CSS_PX_PER_UNIT[unit]


def _bezier(p0: Point, ctrl: list[Point], p1: Point) -> list[Point]:
    """Flatten a quadratic (1 ctrl) or cubic (2 ctrl) Bezier into _BEZIER_SAMPLES segments (excl. p0)."""
    pts = [p0, *ctrl, p1]
    out: list[Point] = []
    for k in range(1, _BEZIER_SAMPLES + 1):
        t = k / _BEZIER_SAMPLES
        # de Casteljau
        level = list(pts)
        while len(level) > 1:
            level = [
                ((1 - t) * a[0] + t * b[0], (1 - t) * a[1] + t * b[1])
                for a, b in zip(level, level[1:])
            ]
        out.append(level[0])
    return out


def _parse_path_d(d: str) -> list[list[Point]]:
    """Parse a path 'd' string into subpaths (one point list per M..Z run), absolute coords.

    Supports M/L/H/V/Z/C/S/Q/T in absolute and relative forms, with implicit command
    repetition. Elliptical arcs (A) are rejected: the reMarkable export never emits
    them and silently approximating one would misplace ink.
    """
    tokens = _TOKEN.findall(d)
    n = len(tokens)
    i = 0

    def num() -> float:
        nonlocal i
        if i >= n or tokens[i].isalpha():
            raise ValueError(f"truncated path data near token {i} in {d[:60]!r}")
        v = float(tokens[i])
        i += 1
        return v

    subpaths: list[list[Point]] = []
    cur: list[Point] = []
    cmd: str | None = None
    x = y = 0.0
    start: Point = (0.0, 0.0)
    prev_ctrl: Point | None = None  # last control point, for S/T reflection
    prev_kind = ""                  # "C" or "Q" when the previous command was a curve

    while i < n:
        t = tokens[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                if cur:
                    subpaths.append(cur)
                cur = []
                x, y = start
                prev_ctrl, prev_kind = None, ""
                continue
        if cmd is None:
            raise ValueError(f"path data must start with a command: {d[:60]!r}")
        if cmd not in "Mm" and not cur:
            raise ValueError(f"path data has a drawing command before any moveto: {d[:60]!r}")

        rel = cmd.islower()
        c = cmd.upper()
        kind = ""
        if c == "M":
            px, py = num(), num()
            if rel:
                px, py = px + x, py + y
            if cur:
                subpaths.append(cur)
            cur = [(px, py)]
            x, y = px, py
            start = (px, py)
            cmd = "l" if rel else "L"  # subsequent coordinate pairs are implicit linetos
        elif c == "L":
            px, py = num(), num()
            if rel:
                px, py = px + x, py + y
            cur.append((px, py))
            x, y = px, py
        elif c == "H":
            px = num()
            if rel:
                px += x
            cur.append((px, y))
            x = px
        elif c == "V":
            py = num()
            if rel:
                py += y
            cur.append((x, py))
            y = py
        elif c in ("C", "S"):
            if c == "C":
                x1, y1 = num(), num()
                if rel:
                    x1, y1 = x1 + x, y1 + y
            else:
                x1, y1 = (2 * x - prev_ctrl[0], 2 * y - prev_ctrl[1]) if prev_kind == "C" and prev_ctrl else (x, y)
            x2, y2, px, py = num(), num(), num(), num()
            if rel:
                x2, y2, px, py = x2 + x, y2 + y, px + x, py + y
            cur.extend(_bezier((x, y), [(x1, y1), (x2, y2)], (px, py)))
            prev_ctrl, kind = (x2, y2), "C"
            x, y = px, py
        elif c in ("Q", "T"):
            if c == "Q":
                x1, y1 = num(), num()
                if rel:
                    x1, y1 = x1 + x, y1 + y
            else:
                x1, y1 = (2 * x - prev_ctrl[0], 2 * y - prev_ctrl[1]) if prev_kind == "Q" and prev_ctrl else (x, y)
            px, py = num(), num()
            if rel:
                px, py = px + x, py + y
            cur.extend(_bezier((x, y), [(x1, y1)], (px, py)))
            prev_ctrl, kind = (x1, y1), "Q"
            x, y = px, py
        elif c == "A":
            raise ValueError("elliptical arc commands are not supported in path data")
        else:  # pragma: no cover - the tokenizer only yields the commands above
            raise ValueError(f"unknown path command {cmd!r}")
        prev_kind = kind
        if not kind:
            prev_ctrl = None

    if cur:
        subpaths.append(cur)
    return [sp for sp in subpaths if len(sp) >= 2]


@dataclass
class InkPath:
    """One `<path>`: its closed subpaths (rings) and the fill rule that combines them."""

    subpaths: list[list[Point]]
    fill_rule: str = "nonzero"  # "nonzero" | "evenodd"


@dataclass
class ParsedSvg:
    viewbox: ViewBox
    template_png: bytes | None
    paths: list[InkPath]


def _presentation_attr(el: ET.Element, name: str) -> str | None:
    """A presentation attribute, from the attribute itself or the inline `style`."""
    value = el.get(name)
    if value is not None:
        return value.strip()
    style = el.get("style") or ""
    for decl in style.split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            if k.strip() == name:
                return v.strip()
    return None


def _collect_paths(el: ET.Element, fill: str, fill_rule: str, out: list[InkPath]) -> None:
    """Depth-first walk collecting drawn <path> elements with inherited fill/fill-rule."""
    tag = _local(el.tag)
    if tag in _NON_INK_CONTAINERS:
        return
    fill = _presentation_attr(el, "fill") or fill
    fill_rule = _presentation_attr(el, "fill-rule") or fill_rule
    if tag == "path":
        if fill.lower() == "none":
            return
        subpaths = _parse_path_d(el.get("d", ""))
        if subpaths:
            out.append(InkPath(subpaths=subpaths, fill_rule="evenodd" if fill_rule == "evenodd" else "nonzero"))
        return
    for child in el:
        _collect_paths(child, fill, fill_rule, out)


def parse_svg(svg_path: str | Path) -> ParsedSvg:
    """Parse a reMarkable SVG export into its viewBox, embedded template PNG, and ink paths.

    Paths are in the SVG's own (possibly centered) coordinates. Raises ValueError when
    the document carries no usable page size.
    """
    root = ET.parse(str(svg_path)).getroot()

    vb = root.get("viewBox")
    if vb:
        parts = re.split(r"[,\s]+", vb.strip())
        if len(parts) != 4:
            raise ValueError(f"malformed viewBox {vb!r} in {svg_path}")
        minx, miny, w, h = (float(v) for v in parts)
    else:
        width, height = root.get("width"), root.get("height")
        if width is None or height is None:
            raise ValueError(f"{svg_path} has neither a viewBox nor width/height; cannot size the page")
        minx, miny = 0.0, 0.0
        w, h = _parse_length(width), _parse_length(height)
    if w <= 0 or h <= 0:
        raise ValueError(f"{svg_path} has a non-positive page size {w}x{h}")

    template: bytes | None = None
    for el in root.iter():
        if _local(el.tag) == "image":
            href = el.get(_XLINK_HREF) or el.get("href") or ""
            if href.startswith("data:image") and "," in href:
                template = base64.b64decode(href.split(",", 1)[1])
                break

    paths: list[InkPath] = []
    _collect_paths(root, "black", "nonzero", paths)
    return ParsedSvg(viewbox=(minx, miny, w, h), template_png=template, paths=paths)


def normalize(points: list[Point], viewbox: ViewBox) -> list[Point]:
    """Translate points by -viewBox.min so they land in page-pixel space (0..w, 0..h)."""
    minx, miny, _, _ = viewbox
    return [(x - minx, y - miny) for x, y in points]


def normalize_path(path: InkPath, viewbox: ViewBox) -> InkPath:
    return InkPath(subpaths=[normalize(sp, viewbox) for sp in path.subpaths], fill_rule=path.fill_rule)


# --------------------------------------------------------------------------- rasterization


def _signed_area(ring: list[Point]) -> float:
    n = len(ring)
    return sum(ring[i][0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * ring[i][1] for i in range(n)) / 2.0


def _perp_dist(p: Point, a: Point, b: Point) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    if (ax, ay) == (bx, by):
        return math.hypot(px - ax, py - ay)
    num = abs((by - ay) * px - (bx - ax) * py + bx * ay - by * ax)
    return num / math.hypot(bx - ax, by - ay)


def _is_collinear(points: list[Point], eps: float = _COLLINEAR_EPS) -> bool:
    """True when every point lies within `eps` of one line (the shape has no area).

    Unlike a signed-area test this cannot be fooled by self-intersecting rings whose
    lobes cancel (a figure-8 has ~zero signed area but plenty of ink).
    """
    p0 = points[0]
    far = max(points, key=lambda p: math.hypot(p[0] - p0[0], p[1] - p0[1]))
    if math.hypot(far[0] - p0[0], far[1] - p0[1]) < eps:
        return True
    return all(_perp_dist(p, p0, far) < eps for p in points)


def _polygon_mask(ring: list[Point], size: tuple[int, int]) -> np.ndarray:
    img = Image.new("1", size, 0)
    ImageDraw.Draw(img).polygon(ring, fill=1)
    # PIL mode-"1" bytes are 0/255; `> 0` yields a canonical, writable bool array
    return np.array(img, dtype=np.uint8) > 0


def rasterize(rings: list[list[Point]], fill_rule: str = "nonzero") -> tuple[np.ndarray, tuple[int, int]]:
    """Rasterize closed rings into a padded binary mask; return (mask, page-px origin of mask[0,0]).

    PIL's own polygon fill is even-odd, so nonzero is implemented by summing each ring's
    mask with its orientation sign (a CW hole inside a CCW outer ring cancels; two
    same-orientation overlaps reinforce).
    """
    rings = [r for r in rings if len(r) >= 3]
    if not rings:
        raise ValueError("rasterize needs at least one ring with 3+ points")
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    ox = math.floor(min(xs)) - _MASK_PAD
    oy = math.floor(min(ys)) - _MASK_PAD
    w = math.ceil(max(xs)) - ox + _MASK_PAD + 1
    h = math.ceil(max(ys)) - oy + _MASK_PAD + 1
    shifted = [[(x - ox, y - oy) for x, y in r] for r in rings]

    if fill_rule == "evenodd":
        mask = np.zeros((h, w), dtype=bool)
        for r in shifted:
            mask ^= _polygon_mask(r, (w, h))
    else:
        winding = np.zeros((h, w), dtype=np.int16)
        for r in shifted:
            sign = -1 if _signed_area(r) < 0 else 1
            winding += sign * _polygon_mask(r, (w, h)).astype(np.int16)
        mask = winding != 0
    return mask, (ox, oy)


# --------------------------------------------------------------------------- skeleton tracing


def _neighbors(p: Pixel, pts: set[Pixel]) -> list[Pixel]:
    r, c = p
    return [
        (r + dr, c + dc)
        for dr in (-1, 0, 1)
        for dc in (-1, 0, 1)
        if (dr or dc) and (r + dr, c + dc) in pts
    ]


def _bfs_farthest(src: Pixel, pts: set[Pixel]) -> tuple[dict[Pixel, Pixel | None], Pixel]:
    """BFS over the pixel graph restricted to `pts`; return (prev-map, farthest-pixel)."""
    prev: dict[Pixel, Pixel | None] = {src: None}
    dq = deque([src])
    last = src
    while dq:
        cur = dq.popleft()
        last = cur
        for nb in _neighbors(cur, pts):
            if nb not in prev:
                prev[nb] = cur
                dq.append(nb)
    return prev, last


def _reconstruct(prev: dict[Pixel, Pixel | None], dst: Pixel) -> list[Pixel]:
    out: list[Pixel] = []
    cur: Pixel | None = dst
    while cur is not None:
        out.append(cur)
        cur = prev[cur]
    out.reverse()
    return out


def _component(seed: Pixel, pts: set[Pixel]) -> set[Pixel]:
    prev, _ = _bfs_farthest(seed, pts)
    return set(prev)


def _longest_path(comp: set[Pixel]) -> tuple[list[Pixel], bool]:
    """Longest simple path through one connected skeleton component.

    Open/branched components -> the longest endpoint-to-endpoint path (double BFS, the
    tree-diameter trick). Closed loops (no endpoints) -> a greedy walk from the
    topmost-leftmost pixel, closed back onto itself. Returns (path, is_closed).
    """
    endpoints = [p for p in comp if len(_neighbors(p, comp)) == 1]
    if endpoints:
        _, far = _bfs_farthest(endpoints[0], comp)
        prev, far2 = _bfs_farthest(far, comp)
        return _reconstruct(prev, far2), False
    start = min(comp)
    path = [start]
    visited = {start}
    cur = start
    while True:
        nxts = [nb for nb in _neighbors(cur, comp) if nb not in visited]
        if not nxts:
            break
        cur = min(nxts)
        path.append(cur)
        visited.add(cur)
    if len(path) >= 3 and start in _neighbors(path[-1], comp):
        path.append(start)  # close the loop
    return path, True


def _skeleton_paths(skel: np.ndarray) -> tuple[list[list[Pixel]], int]:
    """Decompose a 1-px skeleton into ordered pixel paths; return (paths, covered pixel count).

    The longest path is peeled off first (the trunk); what remains splits into
    components (branches, the far side of loops) that are each traced the same way and
    joined back onto the already-traced pixel they touch, so a looped 'e' or a
    self-touching 'l' keeps all its ink instead of only its longest arm. Components
    shorter than _MIN_BRANCH_PX are skeletonization spurs and are dropped (they still
    count against coverage).
    """
    pts = {(int(r), int(c)) for r, c in np.argwhere(skel)}
    remaining = set(pts)
    traced: set[Pixel] = set()
    paths: list[list[Pixel]] = []
    while remaining:
        comp = _component(min(remaining), remaining)
        path, closed = _longest_path(comp)
        if paths and len(path) < _MIN_BRANCH_PX:
            remaining -= comp  # a spur: discard the whole fragment
            continue
        remaining -= set(path)  # peel the path; what is left of comp becomes new components
        if not closed and traced:
            head = [nb for nb in _neighbors(path[0], traced)]
            tail = [nb for nb in _neighbors(path[-1], traced)]
            if head:
                path = [min(head), *path]
            if tail:
                path = [*path, min(tail)]
        traced |= set(path)
        paths.append(path)
    return paths, len(traced & pts)


def _rdp_keep(points: list[Point], eps: float) -> list[int]:
    """Douglas-Peucker simplification; returns the indices of the points to keep.

    Iterative (explicit stack) rather than recursive: a long skeleton path can run to
    thousands of points, and recursive RDP hits O(n) depth on smoothly curving input.
    """
    n = len(points)
    if n < 3:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        a, b = points[start], points[end]
        dmax, idx = 0.0, -1
        for i in range(start + 1, end):
            d = _perp_dist(points[i], a, b)
            if d > dmax:
                dmax, idx = d, i
        if idx != -1 and dmax > eps:
            keep[idx] = True
            stack.append((start, idx))
            stack.append((idx, end))
    return [i for i in range(n) if keep[i]]


def _rdp(points: list[Point], eps: float) -> list[Point]:
    return [points[i] for i in _rdp_keep(points, eps)]


# --------------------------------------------------------------------------- strokes


@dataclass
class PenStroke:
    """One pen stroke: its filled ink outline plus derived centerlines.

    All coordinates are page pixels. `centerlines[i][j]` has ink width `widths[i][j]`
    (measured from the distance transform of the filled outline). `coverage` is the
    fraction of the skeleton the centerlines account for; below LOW_COVERAGE some ink
    (a branch or loop) was lost in tracing and the stroke should be reviewed.
    """

    centerlines: list[list[Point]]
    widths: list[list[float]]
    outline: list[list[Point]] = field(default_factory=list)
    fill_rule: str = "nonzero"
    coverage: float = 1.0
    is_stub: bool = False
    pressures: list[list[float]] | None = None  # device pressure (0..1), only from native sources

    @property
    def points(self) -> list[Point]:
        return [p for line in self.centerlines for p in line]

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """(min_x, min_y, max_x, max_y) over the outline if present, else the centerlines."""
        pts = [p for r in self.outline for p in r] or self.points
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def low_coverage(self) -> bool:
        return self.coverage < LOW_COVERAGE


def centerline(path: InkPath) -> PenStroke | None:
    """Extract pen centerlines from a filled ink path (page-px in, page-px out).

    Returns None for a degenerate path (no area: collinear or fewer than 3 distinct
    points), which the caller counts as dropped.
    """
    rings = [sp for sp in path.subpaths if len(sp) >= 3]
    all_pts = [p for r in rings for p in r]
    if len(all_pts) < 3 or _is_collinear(all_pts):
        return None

    mask, (ox, oy) = rasterize(rings, path.fill_rule)
    if not mask.any():
        return None

    edt = ndimage.distance_transform_edt(mask)
    # method="lee": scikit-image's default 2D skeletonizer (Zhang-Suen, fast_skeletonize)
    # has an out-of-bounds read that intermittently segfaults on valid bordered masks.
    # The Lee 1994 method is a separate compiled module, is stable under stress, and
    # yields an equivalent centerline.
    skel = skeletonize(mask, method="lee") > 0
    n_skel = int(skel.sum())

    if n_skel < _MIN_SKELETON_PX:
        cy, cx = (float(v) for v in np.argwhere(mask).mean(axis=0))
        width = max(1.0, 2.0 * float(edt.max()) - 1.0)
        px, py = cx + ox, cy + oy
        return PenStroke(
            centerlines=[[(px, py), (px + 0.5, py)]],  # centroid stub (valid 2-point contour)
            widths=[[width, width]],
            outline=rings,
            fill_rule=path.fill_rule,
            coverage=1.0,
            is_stub=True,
        )

    pixel_paths, covered = _skeleton_paths(skel)
    centerlines: list[list[Point]] = []
    widths: list[list[float]] = []
    for pp in pixel_paths:
        pts = [(float(c + ox), float(r + oy)) for r, c in pp]
        ws = [max(1.0, 2.0 * float(edt[r, c]) - 1.0) for r, c in pp]
        keep = _rdp_keep(pts, _RDP_EPSILON)
        if len(keep) >= 2:
            centerlines.append([pts[i] for i in keep])
            widths.append([ws[i] for i in keep])
    if not centerlines:
        return None
    return PenStroke(
        centerlines=centerlines,
        widths=widths,
        outline=rings,
        fill_rule=path.fill_rule,
        coverage=covered / n_skel,
    )


# --------------------------------------------------------------------------- page raster


def _paint_mask(arr: np.ndarray, mask: np.ndarray, origin: tuple[int, int]) -> None:
    """Paint `mask` (True = ink) black into `arr` at `origin`, clipping to the page."""
    ox, oy = origin
    h, w = mask.shape
    H, W = arr.shape
    x0, y0 = max(0, ox), max(0, oy)
    x1, y1 = min(W, ox + w), min(H, oy + h)
    if x1 <= x0 or y1 <= y0:
        return
    sub = mask[y0 - oy : y1 - oy, x0 - ox : x1 - ox]
    arr[y0:y1, x0:x1][sub] = 0


def draw_ink(base: Image.Image, strokes: list[PenStroke]) -> Image.Image:
    """Draw strokes as real ink (black) onto a grayscale page image; returns a new image.

    Strokes with an outline are rasterized exactly (their true, pressure-varying
    shape). Strokes without one (native-device sources) are rendered from their
    centerlines with the per-point width, round-joined.
    """
    arr = np.array(base.convert("L"), dtype=np.uint8)
    draw_img: Image.Image | None = None
    for stroke in strokes:
        if stroke.outline:
            mask, origin = rasterize(stroke.outline, stroke.fill_rule)
            _paint_mask(arr, mask, origin)
            continue
        if draw_img is None:
            draw_img = Image.fromarray(arr)
        draw = ImageDraw.Draw(draw_img)
        for line, ws in zip(stroke.centerlines, stroke.widths):
            for (ax, ay), (bx, by), wa, wb in zip(line, line[1:], ws, ws[1:]):
                draw.line([(ax, ay), (bx, by)], fill=0, width=max(1, round((wa + wb) / 2)))
            for (x, y), wv in zip(line, ws):
                r = wv / 2
                if r >= 1:
                    draw.ellipse([x - r, y - r, x + r, y + r], fill=0)
    if draw_img is not None:
        arr = np.array(draw_img, dtype=np.uint8)
    return Image.fromarray(arr)


def compose_raster(
    template: bytes | Image.Image | None,
    strokes: list[PenStroke],
    page_size: tuple[int, int],
) -> Image.Image:
    """Composite real ink (black) over the template raster (or white).

    Returns an 8-bit grayscale ("L") image of `page_size`. A template of a different
    size is resized onto the page; the caller reports that (see RemarkableExport.template_size).
    """
    if template is None:
        base = Image.new("L", page_size, color=255)
    else:
        base = (Image.open(io.BytesIO(template)) if isinstance(template, bytes) else template).convert("L")
        if base.size != page_size:
            base = base.resize(page_size)
    return draw_ink(base, strokes)


# --------------------------------------------------------------------------- entry point


@dataclass
class RemarkableExport:
    """The page raster + pen strokes extracted from a reMarkable export (SVG or native)."""

    page_raster: Image.Image
    strokes: list[PenStroke]
    page_size: tuple[int, int]
    viewbox_offset: tuple[float, float]
    dropped_paths: int
    has_template: bool
    template_size: tuple[int, int] | None = None  # decoded template PNG size, if any

    @property
    def template_resized(self) -> bool:
        """True when the embedded template did not match the viewBox and was resized onto it."""
        return self.template_size is not None and self.template_size != self.page_size

    @property
    def low_coverage_strokes(self) -> int:
        return sum(1 for s in self.strokes if s.low_coverage)

    def summary(self) -> str:
        parts = [
            f"{len(self.strokes)} stroke(s)",
            f"{self.dropped_paths} dropped",
            f"{self.low_coverage_strokes} low-coverage",
            f"template={self.has_template}",
        ]
        if self.template_resized:
            parts.append(f"template {self.template_size} resized to page {self.page_size}")
        return ", ".join(parts)


def load_remarkable_export(svg_path: str | Path) -> RemarkableExport:
    """Ingest a reMarkable SVG export into a page raster + per-stroke centerlines (page-px).

    Raises ValueError if the export contains no ink paths or no page size.
    """
    parsed = parse_svg(svg_path)
    if not parsed.paths:
        raise ValueError(f"no ink paths found in {svg_path}")

    minx, miny, w, h = parsed.viewbox
    page_size = (round(w), round(h))

    strokes: list[PenStroke] = []
    dropped = 0
    for path in parsed.paths:
        stroke = centerline(normalize_path(path, parsed.viewbox))
        if stroke is None:
            dropped += 1
        else:
            strokes.append(stroke)

    template: Image.Image | None = None
    if parsed.template_png is not None:
        template = Image.open(io.BytesIO(parsed.template_png)).convert("L")

    raster = compose_raster(template, strokes, page_size)
    return RemarkableExport(
        page_raster=raster,
        strokes=strokes,
        page_size=page_size,
        viewbox_offset=(minx, miny),
        dropped_paths=dropped,
        has_template=template is not None,
        template_size=template.size if template is not None else None,
    )

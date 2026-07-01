# reMarkable Export Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `ingest-segment`'s synthetic `svg_strokes.py` with a `remarkable_svg.py` adapter that ingests a real reMarkable SVG export (centered `viewBox`, embedded template raster, `fill`-contour ink) and produces the page raster + per-stroke centerline geometry the existing pipeline already consumes.

**Architecture:** A new module `remarkable_svg.py` exposes `load_remarkable_export(svg_path) -> RemarkableExport` plus small testable helpers (`parse_svg`, `normalize`, `centerline`, `compose_raster`). Ink centerlines come from per-path raster skeletonization (`scikit-image`). The page raster composites the vector ink over the decoded template raster. `align.py`, `segment.py`, `candidates_out.py`, and `run.ingest_page` are unchanged; only the CLI front-end and package exports change.

**Tech Stack:** Python 3.12, pydantic v2, Pillow, numpy, **scikit-image** (new — `skeletonize`), stdlib `xml.etree` + `re` for SVG parsing, pytest.

**Spec:** [docs/superpowers/specs/2026-06-30-remarkable-export-adapter-design.md](../specs/2026-06-30-remarkable-export-adapter-design.md)

---

## Conventions (match the existing `ingest-segment` package)

- `from __future__ import annotations` first; module-level named constants for thresholds; concise docstrings; `str | Path` inputs. Mirror `align.py`/`raster.py`/`segment.py`.
- Run tests with **`python3`** (no `python` alias in this environment): `cd packages/ingest-segment && python3 -m pytest -q`. If imports fail, `pip install -e packages/ingest-segment` from the repo root.
- Coordinate convention: a "ring" is a list of `(x, y)` float tuples; a "stroke"/centerline is likewise a `list[tuple[float, float]]`, in page-pixel space. Skeleton pixel coords are `(row, col)` internally and converted to `(x, y) = (col, row)` on the way out.

---

## File Structure

- **Create:** `packages/ingest-segment/src/ingest_segment/remarkable_svg.py` — the adapter (parse → normalize → centerline → compose; `RemarkableExport` dataclass; `load_remarkable_export`).
- **Delete:** `packages/ingest-segment/src/ingest_segment/svg_strokes.py` and `packages/ingest-segment/tests/test_svg_strokes.py` (retired stroke-colored format).
- **Create:** `packages/ingest-segment/tests/test_remarkable_svg.py` — unit + end-to-end synthetic tests.
- **Create:** `packages/ingest-segment/tests/test_remarkable_real.py` — opt-in, path-gated real-export test.
- **Modify:** `packages/ingest-segment/pyproject.toml` — add `scikit-image` dependency.
- **Modify:** `packages/ingest-segment/src/ingest_segment/run.py` — CLI uses the adapter; `--raster` optional; `--svg` self-extracts the raster; drop `_ink_strokes_from_svg`.
- **Modify:** `packages/ingest-segment/src/ingest_segment/__init__.py` — drop `RawStroke`/`parse_svg_strokes`/`separate_ink`; export `load_remarkable_export`/`RemarkableExport`.
- **Modify:** `packages/ingest-segment/tests/test_cli.py` — add a `--svg`-only CLI test.

---

## Task 1: Add the scikit-image dependency

**Files:**
- Modify: `packages/ingest-segment/pyproject.toml`
- Test: `packages/ingest-segment/tests/test_remarkable_svg.py`

- [ ] **Step 1: Write the failing test**

Create `packages/ingest-segment/tests/test_remarkable_svg.py`:

```python
def test_skeletonize_dependency_importable():
    from skimage.morphology import skeletonize  # noqa: F401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_remarkable_svg.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'skimage'`

- [ ] **Step 3: Add the dependency**

In `packages/ingest-segment/pyproject.toml`, add `"scikit-image>=0.22"` to the `dependencies` list (after `"svgpathtools>=1.6",`):

```toml
dependencies = [
    "hwfont-schema",
    "anthropic>=0.40",
    "pillow>=10.0",
    "numpy>=1.26",
    "svgpathtools>=1.6",
    "scikit-image>=0.22",
]
```

- [ ] **Step 4: Install and run the test**

Run: `pip install -e packages/ingest-segment && cd packages/ingest-segment && python3 -m pytest tests/test_remarkable_svg.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/pyproject.toml packages/ingest-segment/tests/test_remarkable_svg.py
git commit -m "build(ingest): add scikit-image for skeletonization"
```

---

## Task 2: `parse_svg` — viewBox, embedded raster, fill rings

**Files:**
- Create: `packages/ingest-segment/src/ingest_segment/remarkable_svg.py`
- Test: `packages/ingest-segment/tests/test_remarkable_svg.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/ingest-segment/tests/test_remarkable_svg.py`:

```python
import base64
import io

from PIL import Image

from ingest_segment.remarkable_svg import parse_svg


def _png_b64(size=(20, 20), color=200):
    img = Image.new("L", size, color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def _write_svg(tmp_path, body, viewbox="-50 0 100 80", with_image=True):
    img_el = (
        f'<image x="-50" y="0" width="100" height="80" '
        f'xlink:href="data:image/png;base64,{_png_b64()}"/>'
        if with_image
        else ""
    )
    svg = (
        f'<svg width="100" height="80" viewBox="{viewbox}" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink">{img_el}{body}</svg>'
    )
    p = tmp_path / "page.svg"
    p.write_text(svg, encoding="utf-8")
    return p


def test_parse_svg_extracts_viewbox_image_and_rings(tmp_path):
    body = (
        '<path fill="#000000" d="M-40 10 L-20 10 L-20 14 L-40 14 "/>'
        '<path fill="#000000" d="M0 30 L10 30 L10 34 L0 34 "/>'
    )
    svg = _write_svg(tmp_path, body)
    viewbox, template_png, rings = parse_svg(svg)

    assert viewbox == (-50.0, 0.0, 100.0, 80.0)
    assert template_png is not None
    assert Image.open(io.BytesIO(template_png)).size == (20, 20)
    assert len(rings) == 2
    # first ring parsed in viewBox coordinates (still centered/negative)
    assert rings[0][0] == (-40.0, 10.0)
    assert len(rings[0]) == 4


def test_parse_svg_without_image(tmp_path):
    svg = _write_svg(tmp_path, '<path fill="#000000" d="M0 0 L10 0 L10 5 L0 5 "/>', with_image=False)
    viewbox, template_png, rings = parse_svg(svg)
    assert template_png is None
    assert len(rings) == 1


def test_parse_svg_missing_viewbox_uses_width_height(tmp_path):
    svg = (
        '<svg width="120" height="90" xmlns="http://www.w3.org/2000/svg">'
        '<path fill="#000000" d="M1 1 L9 1 L9 5 L1 5 "/></svg>'
    )
    p = tmp_path / "p.svg"
    p.write_text(svg, encoding="utf-8")
    viewbox, _, rings = parse_svg(p)
    assert viewbox == (0.0, 0.0, 120.0, 90.0)
    assert len(rings) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_remarkable_svg.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest_segment.remarkable_svg'`

- [ ] **Step 3: Implement `parse_svg`**

Create `packages/ingest-segment/src/ingest_segment/remarkable_svg.py`:

```python
from __future__ import annotations

import base64
import re
import xml.etree.ElementTree as ET
from pathlib import Path

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_NUM = re.compile(r"-?\d+(?:\.\d+)?")

# a viewBox tuple is (min_x, min_y, width, height)
ViewBox = tuple[float, float, float, float]


def _local(tag: str) -> str:
    """Strip an XML namespace, e.g. '{http://...}path' -> 'path'."""
    return tag.rsplit("}", 1)[-1]


def _parse_path_d(d: str) -> list[tuple[float, float]]:
    """Parse an M/L path 'd' string into an ordered ring of (x, y) points.

    The reMarkable export uses only absolute M/L line segments, so we take the
    coordinate numbers in order and pair them.
    """
    nums = [float(n) for n in _NUM.findall(d)]
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


def parse_svg(svg_path: str | Path) -> tuple[ViewBox, bytes | None, list[list[tuple[float, float]]]]:
    """Parse a reMarkable SVG export into (viewBox, template PNG bytes | None, fill rings).

    Rings are one per `<path>`, in the SVG's own (possibly centered) coordinates.
    """
    root = ET.parse(str(svg_path)).getroot()

    vb = root.get("viewBox")
    if vb:
        minx, miny, w, h = (float(v) for v in vb.split())
    else:
        minx, miny = 0.0, 0.0
        w, h = float(root.get("width", "0")), float(root.get("height", "0"))

    template: bytes | None = None
    rings: list[list[tuple[float, float]]] = []
    for el in root.iter():
        tag = _local(el.tag)
        if tag == "image" and template is None:
            href = el.get(_XLINK_HREF) or el.get("href") or ""
            if href.startswith("data:image") and "," in href:
                template = base64.b64decode(href.split(",", 1)[1])
        elif tag == "path":
            ring = _parse_path_d(el.get("d", ""))
            if len(ring) >= 2:
                rings.append(ring)

    return (minx, miny, w, h), template, rings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_remarkable_svg.py -q`
Expected: PASS (4 tests: the dependency test + 3 parse tests)

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/remarkable_svg.py packages/ingest-segment/tests/test_remarkable_svg.py
git commit -m "feat(ingest): parse reMarkable SVG (viewBox, embedded raster, fill rings)"
```

---

## Task 3: `normalize` — shift viewBox-centered coords into page-pixels

**Files:**
- Modify: `packages/ingest-segment/src/ingest_segment/remarkable_svg.py`
- Test: `packages/ingest-segment/tests/test_remarkable_svg.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/ingest-segment/tests/test_remarkable_svg.py`:

```python
from ingest_segment.remarkable_svg import normalize


def test_normalize_shifts_by_viewbox_min():
    ring = [(-40.0, 10.0), (-20.0, 10.0)]
    out = normalize(ring, (-50.0, 0.0, 100.0, 80.0))
    assert out == [(10.0, 10.0), (30.0, 10.0)]  # +50 in x, +0 in y


def test_normalize_identity_when_origin_zero():
    ring = [(5.0, 5.0), (9.0, 12.0)]
    assert normalize(ring, (0.0, 0.0, 100.0, 100.0)) == ring
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_remarkable_svg.py::test_normalize_shifts_by_viewbox_min -q`
Expected: FAIL with `ImportError: cannot import name 'normalize'`

- [ ] **Step 3: Implement `normalize`**

Append to `packages/ingest-segment/src/ingest_segment/remarkable_svg.py`:

```python
def normalize(points: list[tuple[float, float]], viewbox: ViewBox) -> list[tuple[float, float]]:
    """Translate points by -viewBox.min so they land in page-pixel space (0..w, 0..h)."""
    minx, miny, _, _ = viewbox
    return [(x - minx, y - miny) for x, y in points]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_remarkable_svg.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/remarkable_svg.py packages/ingest-segment/tests/test_remarkable_svg.py
git commit -m "feat(ingest): normalize viewBox-centered coordinates to page-pixels"
```

---

## Task 4: `centerline` — rasterize → skeletonize → trace → simplify

**Files:**
- Modify: `packages/ingest-segment/src/ingest_segment/remarkable_svg.py`
- Test: `packages/ingest-segment/tests/test_remarkable_svg.py`

This task builds the skeletonization core and its helpers (`_trace`, `_rdp`) for the common open-curve case. Task 5 adds degenerate + closed-loop handling.

- [ ] **Step 1: Write the failing test**

Append to `packages/ingest-segment/tests/test_remarkable_svg.py`:

```python
from ingest_segment.remarkable_svg import centerline


def test_centerline_of_filled_bar_is_horizontal_midline():
    # a 40-wide x 8-tall filled rectangle -> centerline ~ horizontal line at mid-height
    ring = [(10.0, 20.0), (50.0, 20.0), (50.0, 28.0), (10.0, 28.0)]
    line = centerline(ring)
    assert line is not None and len(line) >= 2
    xs = [p[0] for p in line]
    ys = [p[1] for p in line]
    assert min(xs) < 16 and max(xs) > 44          # spans the bar horizontally
    assert max(ys) - min(ys) <= 3                  # roughly flat, near mid-height (~24)
    assert 22 <= sum(ys) / len(ys) <= 26


def test_centerline_of_vertical_bar_is_vertical():
    ring = [(30.0, 10.0), (36.0, 10.0), (36.0, 60.0), (30.0, 60.0)]
    line = centerline(ring)
    ys = [p[1] for p in line]
    xs = [p[0] for p in line]
    assert min(ys) < 16 and max(ys) > 54
    assert max(xs) - min(xs) <= 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_remarkable_svg.py::test_centerline_of_filled_bar_is_horizontal_midline -q`
Expected: FAIL with `ImportError: cannot import name 'centerline'`

- [ ] **Step 3: Implement `centerline` + helpers**

Append to `packages/ingest-segment/src/ingest_segment/remarkable_svg.py`. Add these imports to the top import block:

```python
import io
import math
from collections import deque

import numpy as np
from PIL import Image, ImageDraw
from skimage.morphology import skeletonize
```

Then append:

```python
_MASK_PAD = 3          # padding around a path's mask, in pixels
_MIN_SKELETON_PX = 4   # skeletons smaller than this collapse to a centroid stub
_RDP_EPSILON = 1.0     # Douglas-Peucker simplification tolerance, in pixels


def _neighbors(p: tuple[int, int], pts: set[tuple[int, int]]) -> list[tuple[int, int]]:
    r, c = p
    return [
        (r + dr, c + dc)
        for dr in (-1, 0, 1)
        for dc in (-1, 0, 1)
        if (dr or dc) and (r + dr, c + dc) in pts
    ]


def _bfs_farthest(src, pts):
    """BFS over the skeleton pixel graph; return (prev-map, farthest-pixel)."""
    prev = {src: None}
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


def _reconstruct(prev, dst):
    out = []
    cur = dst
    while cur is not None:
        out.append(cur)
        cur = prev[cur]
    out.reverse()
    return out


def _trace(skel: np.ndarray) -> list[tuple[int, int]]:
    """Trace a 1-px skeleton into an ordered list of (x, y) points.

    Open/branched skeletons -> the longest endpoint-to-endpoint path (double BFS,
    the tree-diameter trick). Closed loops (no endpoints) -> a greedy walk from the
    topmost-leftmost pixel.
    """
    pts = {(int(r), int(c)) for r, c in np.argwhere(skel)}
    if not pts:
        return []
    endpoints = [p for p in pts if len(_neighbors(p, pts)) == 1]
    if endpoints:
        _, far = _bfs_farthest(endpoints[0], pts)
        prev, far2 = _bfs_farthest(far, pts)
        path = _reconstruct(prev, far2)
    else:
        start = min(pts)  # topmost-leftmost (row, col)
        path = [start]
        visited = {start}
        cur = start
        while True:
            nxts = [nb for nb in _neighbors(cur, pts) if nb not in visited]
            if not nxts:
                break
            cur = min(nxts)
            path.append(cur)
            visited.add(cur)
    return [(c, r) for (r, c) in path]  # (x, y)


def _perp_dist(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    if (ax, ay) == (bx, by):
        return math.hypot(px - ax, py - ay)
    num = abs((by - ay) * px - (bx - ax) * py + bx * ay - by * ax)
    return num / math.hypot(bx - ax, by - ay)


def _rdp(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    """Douglas-Peucker polyline simplification."""
    if len(points) < 3:
        return points
    start, end = points[0], points[-1]
    dmax, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        d = _perp_dist(points[i], start, end)
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        left = _rdp(points[: idx + 1], eps)
        right = _rdp(points[idx:], eps)
        return left[:-1] + right
    return [start, end]


def centerline(ring: list[tuple[float, float]]) -> list[tuple[float, float]] | None:
    """Extract one pen centerline from a filled ink ring (page-px in, page-px out).

    Returns None for a zero-area ring (dropped by the caller).
    """
    if len(ring) < 3:
        return ring if len(ring) >= 2 else None

    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    minx, miny = math.floor(min(xs)), math.floor(min(ys))
    w = math.ceil(max(xs)) - minx + 2 * _MASK_PAD + 1
    h = math.ceil(max(ys)) - miny + 2 * _MASK_PAD + 1

    mask_img = Image.new("1", (w, h), 0)
    ImageDraw.Draw(mask_img).polygon(
        [(x - minx + _MASK_PAD, y - miny + _MASK_PAD) for x, y in ring], fill=1
    )
    mask = np.asarray(mask_img, dtype=bool)
    if not mask.any():
        return None  # zero-area (collinear) ring

    skel = skeletonize(mask)
    if int(skel.sum()) < _MIN_SKELETON_PX:
        cy, cx = (float(v) for v in np.argwhere(mask).mean(axis=0))
        px = cx + minx - _MASK_PAD
        py = cy + miny - _MASK_PAD
        return [(px, py), (px + 0.5, py)]  # centroid stub (valid 2-point contour)

    traced = [(x + minx - _MASK_PAD, y + miny - _MASK_PAD) for x, y in _trace(skel)]
    simplified = _rdp(traced, _RDP_EPSILON)
    return simplified if len(simplified) >= 2 else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_remarkable_svg.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/remarkable_svg.py packages/ingest-segment/tests/test_remarkable_svg.py
git commit -m "feat(ingest): centerline extraction via skeletonize + trace + RDP"
```

---

## Task 5: `centerline` degenerate + `_trace` closed-loop cases

**Files:**
- Test: `packages/ingest-segment/tests/test_remarkable_svg.py`

The code from Task 4 already handles these (zero-area → None, small blob → stub, cycle → greedy walk). This task pins that behavior with tests.

- [ ] **Step 1: Write the failing/regression tests**

Append to `packages/ingest-segment/tests/test_remarkable_svg.py`:

```python
import numpy as np

from ingest_segment.remarkable_svg import _trace


def test_centerline_zero_area_ring_returns_none():
    assert centerline([(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]) is None  # collinear -> no area


def test_centerline_small_blob_returns_stub():
    ring = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]  # tiny -> stub
    line = centerline(ring)
    assert line is not None and len(line) >= 2


def test_trace_closed_loop_walks_the_ring():
    # 5x5 square ring skeleton (border True, center False) -> a closed loop
    skel = np.zeros((5, 5), dtype=bool)
    skel[0, :] = skel[4, :] = skel[:, 0] = skel[:, 4] = True
    skel[1:4, 1:4] = False
    traced = _trace(skel)
    assert len(traced) >= 12  # walks most of the 16-pixel perimeter
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_remarkable_svg.py -q`
Expected: PASS (these lock in Task 4's degenerate/loop branches)

- [ ] **Step 3: Commit**

```bash
git add packages/ingest-segment/tests/test_remarkable_svg.py
git commit -m "test(ingest): pin centerline degenerate + closed-loop behavior"
```

---

## Task 6: `compose_raster` — ink over template, ink-on-white fallback

**Files:**
- Modify: `packages/ingest-segment/src/ingest_segment/remarkable_svg.py`
- Test: `packages/ingest-segment/tests/test_remarkable_svg.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/ingest-segment/tests/test_remarkable_svg.py`:

```python
from ingest_segment.remarkable_svg import compose_raster


def _template_png_with_corner_dot():
    img = Image.new("L", (100, 80), color=255)
    ImageDraw.Draw(img).ellipse([2, 2, 10, 10], fill=0)  # a "fiducial" dot near TL
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_compose_over_template_keeps_fiducials_and_adds_ink():
    template = _template_png_with_corner_dot()
    strokes = [[(20.0, 40.0), (80.0, 40.0)]]  # horizontal ink line
    raster = compose_raster(template, strokes, (100, 80))
    arr = np.asarray(raster.convert("L"))
    assert raster.size == (100, 80)
    assert arr[6, 6] < 128           # fiducial dot preserved
    assert arr[40, 50] < 128         # ink drawn along the stroke


def test_compose_without_template_is_ink_on_white():
    strokes = [[(10.0, 10.0), (10.0, 70.0)]]
    raster = compose_raster(None, strokes, (100, 80))
    arr = np.asarray(raster.convert("L"))
    assert raster.size == (100, 80)
    assert arr[70, 20] == 255        # background white where there is no ink
    assert arr[40, 10] < 128         # ink present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_remarkable_svg.py::test_compose_over_template_keeps_fiducials_and_adds_ink -q`
Expected: FAIL with `ImportError: cannot import name 'compose_raster'`

- [ ] **Step 3: Implement `compose_raster`**

Append to `packages/ingest-segment/src/ingest_segment/remarkable_svg.py`:

```python
_INK_WIDTH = 3  # px; render centerlines thick enough for vision to read


def compose_raster(
    template_png: bytes | None,
    strokes: list[list[tuple[float, float]]],
    page_size: tuple[int, int],
) -> Image.Image:
    """Composite ink centerlines (black) over the template raster (or white)."""
    if template_png is not None:
        base = Image.open(io.BytesIO(template_png)).convert("L")
        if base.size != page_size:
            base = base.resize(page_size)
    else:
        base = Image.new("L", page_size, color=255)

    draw = ImageDraw.Draw(base)
    for stroke in strokes:
        if len(stroke) >= 2:
            draw.line([(x, y) for x, y in stroke], fill=0, width=_INK_WIDTH)
    return base
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_remarkable_svg.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/remarkable_svg.py packages/ingest-segment/tests/test_remarkable_svg.py
git commit -m "feat(ingest): compose page raster from ink over template"
```

---

## Task 7: `RemarkableExport` + `load_remarkable_export` (end-to-end)

**Files:**
- Modify: `packages/ingest-segment/src/ingest_segment/remarkable_svg.py`
- Test: `packages/ingest-segment/tests/test_remarkable_svg.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/ingest-segment/tests/test_remarkable_svg.py`:

```python
from ingest_segment.remarkable_svg import RemarkableExport, load_remarkable_export


def test_load_remarkable_export_end_to_end(tmp_path):
    # centered viewBox; two filled-bar "strokes"; one collinear (zero-area) path to drop
    body = (
        '<path fill="#000000" d="M-40 20 L0 20 L0 28 L-40 28 "/>'
        '<path fill="#000000" d="M10 40 L40 40 L40 46 L10 46 "/>'
        '<path fill="#000000" d="M-10 60 L0 60 L10 60 "/>'
    )
    svg = _write_svg(tmp_path, body, viewbox="-50 0 100 80")

    exp = load_remarkable_export(svg)
    assert isinstance(exp, RemarkableExport)
    assert exp.page_size == (100, 80)
    assert exp.viewbox_offset == (-50.0, 0.0)
    assert exp.has_template is True
    assert exp.dropped_paths == 1                 # the collinear path
    assert len(exp.strokes) == 2                  # two real bars
    # strokes are normalized into page-px (0..100), no negative x
    all_x = [x for s in exp.strokes for x, _ in s]
    assert min(all_x) >= 0 and max(all_x) <= 100
    assert exp.page_raster.size == (100, 80)


def test_load_remarkable_export_without_template(tmp_path):
    svg = _write_svg(tmp_path, '<path fill="#000000" d="M0 0 L40 0 L40 6 L0 6 "/>', with_image=False)
    exp = load_remarkable_export(svg)
    assert exp.has_template is False
    assert exp.page_raster.size == (100, 80)
    assert len(exp.strokes) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_remarkable_svg.py::test_load_remarkable_export_end_to_end -q`
Expected: FAIL with `ImportError: cannot import name 'RemarkableExport'`

- [ ] **Step 3: Implement the dataclass + entry point**

Append to `packages/ingest-segment/src/ingest_segment/remarkable_svg.py`. Add `from dataclasses import dataclass` to the top import block, then:

```python
@dataclass
class RemarkableExport:
    """The page raster + centerline strokes extracted from a reMarkable SVG export."""

    page_raster: Image.Image
    strokes: list[list[tuple[float, float]]]
    page_size: tuple[int, int]
    viewbox_offset: tuple[float, float]
    dropped_paths: int
    has_template: bool


def load_remarkable_export(svg_path: str | Path) -> RemarkableExport:
    """Ingest a reMarkable SVG export into a page raster + per-stroke centerlines (page-px).

    Raises ValueError if the export contains no ink paths.
    """
    viewbox, template_png, rings = parse_svg(svg_path)
    if not rings:
        raise ValueError(f"no ink paths found in {svg_path}")

    minx, miny, w, h = viewbox
    page_size = (round(w), round(h))

    strokes: list[list[tuple[float, float]]] = []
    dropped = 0
    for ring in rings:
        line = centerline(normalize(ring, viewbox))
        if line is None:
            dropped += 1
        else:
            strokes.append(line)

    raster = compose_raster(template_png, strokes, page_size)
    return RemarkableExport(
        page_raster=raster,
        strokes=strokes,
        page_size=page_size,
        viewbox_offset=(minx, miny),
        dropped_paths=dropped,
        has_template=template_png is not None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_remarkable_svg.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/remarkable_svg.py packages/ingest-segment/tests/test_remarkable_svg.py
git commit -m "feat(ingest): load_remarkable_export end-to-end adapter"
```

---

## Task 8: Retire `svg_strokes` and update package exports

**Files:**
- Delete: `packages/ingest-segment/src/ingest_segment/svg_strokes.py`
- Delete: `packages/ingest-segment/tests/test_svg_strokes.py`
- Modify: `packages/ingest-segment/src/ingest_segment/__init__.py`

- [ ] **Step 1: Delete the retired module and its test**

```bash
git rm packages/ingest-segment/src/ingest_segment/svg_strokes.py packages/ingest-segment/tests/test_svg_strokes.py
```

- [ ] **Step 2: Update `__init__.py`**

In `packages/ingest-segment/src/ingest_segment/__init__.py`, replace the `svg_strokes` import line:

```python
from ingest_segment.svg_strokes import RawStroke, parse_svg_strokes, separate_ink
```

with:

```python
from ingest_segment.remarkable_svg import RemarkableExport, load_remarkable_export
```

In `__all__`, remove `"RawStroke"`, `"parse_svg_strokes"`, `"separate_ink"` and add `"RemarkableExport"`, `"load_remarkable_export"`.

- [ ] **Step 3: Verify the package imports and the suite is green (minus run.py, fixed next task)**

Run: `cd packages/ingest-segment && python3 -c "import ingest_segment as m; print('load_remarkable_export' in m.__all__, hasattr(m, 'load_remarkable_export'))"`
Expected: `True True`

Run: `cd packages/ingest-segment && python3 -m pytest -q 2>&1 | tail -5`
Expected: FAIL — `run.py` still imports from `svg_strokes` (fixed in Task 9). Note the failure is an ImportError in `run`/`test_run`/`test_cli`, confirming what Task 9 must fix.

- [ ] **Step 4: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/__init__.py packages/ingest-segment/src/ingest_segment/svg_strokes.py packages/ingest-segment/tests/test_svg_strokes.py
git commit -m "refactor(ingest): retire svg_strokes; export reMarkable adapter"
```

---

## Task 9: Wire the adapter into the CLI (`--svg` self-extracts the raster)

**Files:**
- Modify: `packages/ingest-segment/src/ingest_segment/run.py`
- Modify: `packages/ingest-segment/tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/ingest-segment/tests/test_cli.py`:

```python
import base64 as _b64
import io as _io

import ingest_segment.run as run_mod
from hwfont_schema import BBox, CaptureSidecar, Fiducial, Page, Region
from ingest_segment.run import main
from ingest_segment.segment import VisionBox, VisionResult
from PIL import Image, ImageDraw


def _remarkable_svg_and_sidecar(tmp_path):
    # template raster (page-px 200x200) with 4 corner fiducial dots
    tpl = Image.new("L", (200, 200), color=255)
    dt = ImageDraw.Draw(tpl)
    for x, y in [(20, 20), (180, 20), (20, 180), (180, 180)]:
        dt.ellipse([x - 8, y - 8, x + 8, y + 8], fill=0)
    buf = _io.BytesIO()
    tpl.save(buf, format="PNG")
    href = "data:image/png;base64," + _b64.standard_b64encode(buf.getvalue()).decode("ascii")

    # one filled-bar stroke inside the region, in centered viewBox coords (x offset -100)
    svg = (
        f'<svg width="200" height="200" viewBox="-100 0 200 200" '
        f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        f'<image x="-100" y="0" width="200" height="200" xlink:href="{href}"/>'
        f'<path fill="#000000" d="M-60 95 L-20 95 L-20 101 L-60 101 "/>'
        f'</svg>'
    )
    svg_path = tmp_path / "page1.svg"
    svg_path.write_text(svg, encoding="utf-8")

    page = Page(
        id="p0", width_px=200, height_px=200, dpi=226,
        fiducials=[
            Fiducial(id="tl", x=20, y=20), Fiducial(id="tr", x=180, y=20),
            Fiducial(id="bl", x=20, y=180), Fiducial(id="br", x=180, y=180),
        ],
        regions=[Region(
            id="p0-r0", expected_transcript="hi", baseline_y=110.0,
            bbox=BBox(x=40.0, y=80.0, w=120.0, h=40.0), expected_units=["h", "i"],
        )],
    )
    sc = tmp_path / "capture.sidecar.json"
    sc.write_text(CaptureSidecar(pages=[page]).model_dump_json(), encoding="utf-8")
    return svg_path, sc


def test_cli_ingests_remarkable_svg_without_raster(tmp_path, monkeypatch):
    svg_path, sc = _remarkable_svg_and_sidecar(tmp_path)

    def fake_vision(crop_png, region):
        return VisionResult(boxes=[
            VisionBox(label="h", kind="single", x=0, y=0, w=50, h=40, confidence=0.9),
            VisionBox(label="i", kind="single", x=60, y=0, w=40, h=40, confidence=0.4),
        ])

    monkeypatch.setattr(run_mod, "_build_vision_client", lambda model: fake_vision)

    out = tmp_path / "out"
    rc = main(["--svg", str(svg_path), "--sidecar", str(sc), "--out", str(out)])
    assert rc == 0
    assert (out / "candidates.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_cli.py::test_cli_ingests_remarkable_svg_without_raster -q`
Expected: FAIL — currently `--raster` is required and `main` imports the deleted `svg_strokes` (ImportError), and `--svg` is treated as raw ink.

- [ ] **Step 3: Update `run.py`**

In `packages/ingest-segment/src/ingest_segment/run.py`:

Replace the import line:

```python
from ingest_segment.svg_strokes import parse_svg_strokes, separate_ink
```

with:

```python
from ingest_segment.remarkable_svg import load_remarkable_export
```

Delete the `_ink_strokes_from_svg` helper (lines defining it):

```python
def _ink_strokes_from_svg(svg_path: str) -> list[list[tuple[float, float]]]:
    ink = separate_ink(parse_svg_strokes(svg_path))
    return [s.points for s in ink]
```

Change the `--raster` argument to optional and update `--svg` help:

```python
    parser.add_argument("--raster", default=None, help="page raster PNG (optional; extracted from --svg if omitted)")
    parser.add_argument("--sidecar", required=True, help="capture.sidecar.json (Contract X)")
    parser.add_argument("--svg", default=None, help="reMarkable SVG export (self-contains the page raster + ink)")
```

Replace the raster-loading + svg block (from `raster = load_raster(args.raster)` through the `if args.svg: ... else: ...` block, i.e. the current lines that load the raster, check its size, and build `strokes_export`) with:

```python
    export = None
    if args.svg:
        export = load_remarkable_export(args.svg)
        print(
            f"reMarkable export: {len(export.strokes)} stroke(s), "
            f"{export.dropped_paths} dropped, template={export.has_template}"
        )

    if args.raster:
        raster = load_raster(args.raster)
    elif export is not None:
        raster = export.page_raster
    else:
        print("provide --raster and/or --svg (a reMarkable export)")
        return 1

    strokes_export = export.strokes if export is not None else []
    export_size = raster.size

    if raster.size != (page.width_px, page.height_px):
        # not fatal: the fiducial affine reconciles the export's pixel space to the sidecar's
        print(
            f"note: raster size {raster.size} != sidecar page size "
            f"({page.width_px}, {page.height_px}); alignment will reconcile."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_cli.py tests/test_run.py -q`
Expected: PASS (the new `--svg` test plus the existing `--raster` CLI test)

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/run.py packages/ingest-segment/tests/test_cli.py
git commit -m "feat(ingest): CLI ingests reMarkable SVG exports (raster optional)"
```

---

## Task 10: Opt-in real-export regression test

**Files:**
- Create: `packages/ingest-segment/tests/test_remarkable_real.py`

Gated on an env var so a real handwriting export is never committed but can be exercised locally.

- [ ] **Step 1: Write the gated test**

Create `packages/ingest-segment/tests/test_remarkable_real.py`:

```python
import os

import pytest

from ingest_segment.remarkable_svg import load_remarkable_export

_EXPORT = os.environ.get("HWF_REMARKABLE_SVG")

pytestmark = pytest.mark.skipif(
    not (_EXPORT and os.path.exists(_EXPORT)),
    reason="set HWF_REMARKABLE_SVG to a real reMarkable export path to run this test",
)


def test_real_export_yields_strokes_and_raster():
    exp = load_remarkable_export(_EXPORT)
    assert len(exp.strokes) > 0
    assert exp.page_raster.size[0] > 0 and exp.page_raster.size[1] > 0
    # centerlines are within the page bounds
    w, h = exp.page_size
    for stroke in exp.strokes:
        for x, y in stroke:
            assert -1 <= x <= w + 1 and -1 <= y <= h + 1
```

- [ ] **Step 2: Verify it skips without the env var**

Run: `cd packages/ingest-segment && python3 -m pytest tests/test_remarkable_real.py -q`
Expected: `1 skipped`

- [ ] **Step 3: (Optional, manual) run against a real export**

Run: `cd packages/ingest-segment && HWF_REMARKABLE_SVG="/path/to/export.svg" python3 -m pytest tests/test_remarkable_real.py -q`
Expected: PASS (when a real export path is provided)

- [ ] **Step 4: Commit**

```bash
git add packages/ingest-segment/tests/test_remarkable_real.py
git commit -m "test(ingest): opt-in real reMarkable export regression test"
```

---

## Task 11: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run all three package suites**

Run:
```bash
pip install -e packages/hwfont-schema -e packages/capture-template -e packages/ingest-segment
for p in hwfont-schema capture-template ingest-segment; do echo "== $p =="; (cd packages/$p && python3 -m pytest -q 2>&1 | tail -1); done
```
Expected: all green; `ingest-segment` shows the real-export test skipped and (if `ANTHROPIC_API_KEY` is unset) the vision integration test skipped. No reference to `svg_strokes` remains.

- [ ] **Step 2: Confirm no lingering references to the retired module**

Run: `git grep -n "svg_strokes\|parse_svg_strokes\|separate_ink\|RawStroke" -- packages/ingest-segment || echo "(clean)"`
Expected: `(clean)`

- [ ] **Step 3: Commit (if any stray fixups were needed)**

```bash
git add -A packages/ingest-segment
git commit -m "chore(ingest): finalize reMarkable adapter migration" || echo "nothing to commit"
```

(Use explicit paths, not `git add -A` at repo root, to avoid staging untracked files.)

---

## Self-Review notes (checked against the spec)

- **Parsing** (spec §Architecture, §Coordinate Normalization): `parse_svg` extracts viewBox / embedded PNG / fill rings (Task 2); `normalize` shifts by `-viewBox.min` (Task 3); `viewBox`-absent fallback covered (Task 2 test). ✅
- **Centerline extraction** (spec §Centerline Extraction): rasterize → `skeletonize` → `_trace` (open/branched double-BFS; closed-loop greedy walk) → `_rdp`; degenerate stub + zero-area drop (Tasks 4–5). ✅
- **Page raster** (spec §Page Raster): composite ink over template; ink-on-white fallback (Task 6). ✅
- **Entry point** (spec §Architecture): `RemarkableExport` with all documented fields; `dropped_paths`, `has_template`; no-ink error (Task 7). ✅
- **Module replacement** (spec Decision 3): delete `svg_strokes.py` + test, update `__init__` exports (Task 8). ✅
- **CLI** (spec §CLI / Integration): `--raster` optional, `--svg` self-extracts raster, size-mismatch downgraded to a note, summary reports dropped/template (Task 9). ✅
- **Error handling** (spec §Error Handling): no-image fallback (Task 6/7), no-paths error (Task 7), viewBox-absent (Task 2), degenerate drop/count (Tasks 4–5), scikit-image import error surfaces naturally at entry (Task 1 ensures it's installed). ✅
- **Testing** (spec §Testing): synthetic committed unit + end-to-end tests (Tasks 2–7), replace `test_svg_strokes.py` (Task 8), opt-in gated real test (Task 10). ✅
- **Type consistency:** `parse_svg` returns `(ViewBox, bytes|None, rings)`; `normalize(points, viewbox)`; `centerline(ring) -> list|None`; `compose_raster(png|None, strokes, page_size)`; `load_remarkable_export -> RemarkableExport`. `run.ingest_page` still receives `raster` + `page_strokes_export` (adapter strokes are in the export's page-px; the fiducial affine reconciles export space → sidecar space — unchanged downstream). ✅
- **Note on `centerline` signature:** the spec sketched `centerline(fill_ring, page_size)`; the implementation uses `centerline(ring)` (a tight local mask needs no page size). Behavior is identical; the unused parameter was dropped to avoid a dead argument.

## Out of scope (per spec)

Contract X sidecar regeneration for an existing booklet; `font-gen` autotrace; native `.rm`; pen pressure / true direction / temporal ordering beyond document order; multi-subpath-per-path and multi-page-per-file.

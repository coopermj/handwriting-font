from __future__ import annotations

import base64
import io
import math
import re
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from skimage.morphology import skeletonize

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
        minx, miny, w, h = (float(v) for v in re.split(r"[,\s]+", vb.strip()))
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


def normalize(points: list[tuple[float, float]], viewbox: ViewBox) -> list[tuple[float, float]]:
    """Translate points by -viewBox.min so they land in page-pixel space (0..w, 0..h)."""
    minx, miny, _, _ = viewbox
    return [(x - minx, y - miny) for x, y in points]


_MASK_PAD = 3          # blank border around a path's mask so edge ink isn't clipped by skeletonize
_MIN_SKELETON_PX = 4   # skeletons smaller than this collapse to a centroid stub
_RDP_EPSILON = 1.0     # Douglas-Peucker simplification tolerance, in pixels
_MIN_RING_AREA = 1.0   # rings with less filled area than this (px^2) are dropped


def _neighbors(p: tuple[int, int], pts: set[tuple[int, int]]) -> list[tuple[int, int]]:
    r, c = p
    return [
        (r + dr, c + dc)
        for dr in (-1, 0, 1)
        for dc in (-1, 0, 1)
        if (dr or dc) and (r + dr, c + dc) in pts
    ]


def _bfs_farthest(
    src: tuple[int, int], pts: set[tuple[int, int]]
) -> tuple[dict[tuple[int, int], tuple[int, int] | None], tuple[int, int]]:
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


def _reconstruct(
    prev: dict[tuple[int, int], tuple[int, int] | None], dst: tuple[int, int]
) -> list[tuple[int, int]]:
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


def _perp_dist(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    (px, py), (ax, ay), (bx, by) = p, a, b
    if (ax, ay) == (bx, by):
        return math.hypot(px - ax, py - ay)
    num = abs((by - ay) * px - (bx - ax) * py + bx * ay - by * ax)
    return num / math.hypot(bx - ax, by - ay)


def _rdp(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    """Douglas-Peucker polyline simplification.

    Iterative (explicit stack) rather than recursive: a long skeleton path can run to
    thousands of points, and recursive RDP hits O(n) depth on smoothly curving input,
    which would blow Python's recursion limit.
    """
    n = len(points)
    if n < 3:
        return points
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
    return [points[i] for i in range(n) if keep[i]]


def centerline(ring: list[tuple[float, float]]) -> list[tuple[float, float]] | None:
    """Extract one pen centerline from a filled ink ring (page-px in, page-px out).

    Returns None for a zero-area ring (dropped by the caller).
    """
    if len(ring) < 3:
        return ring if len(ring) >= 2 else None

    # shoelace area: a collinear/zero-area ring has no ink to skeletonize.
    # (PIL still draws collinear points as a 1px line, so the mask check below
    # would not catch it — drop it explicitly here.)
    area = abs(
        sum(
            ring[i][0] * ring[(i + 1) % len(ring)][1]
            - ring[(i + 1) % len(ring)][0] * ring[i][1]
            for i in range(len(ring))
        )
    ) / 2.0
    if area < _MIN_RING_AREA:
        return None

    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    minx, miny = math.floor(min(xs)), math.floor(min(ys))
    w = math.ceil(max(xs)) - minx + 2 * _MASK_PAD + 1
    h = math.ceil(max(ys)) - miny + 2 * _MASK_PAD + 1

    mask_img = Image.new("1", (w, h), 0)
    ImageDraw.Draw(mask_img).polygon(
        [(x - minx + _MASK_PAD, y - miny + _MASK_PAD) for x, y in ring], fill=1
    )
    # PIL mode-"1" bytes are 0/255; normalize to canonical 0/1 bool. Some
    # scikit-image skeletonize builds read raw bytes and misbehave on 255-valued
    # bools, so `> 0` yields a writable array with canonical byte values.
    mask = np.array(mask_img, dtype=np.uint8) > 0
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

"""Build synthetic reMarkable-shaped SVG exports for tests.

Real handwriting stays out of the repo; this generates the same *shape* of export the
device produces (centered viewBox, embedded template PNG, one filled outline path per
pen stroke, letters with counters as outer-ring + hole subpaths) from geometry.
"""

from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

Point = tuple[float, float]


# --------------------------------------------------------------------------- geometry


def sweep(polyline: list[Point], width: float) -> list[Point]:
    """The outline of a pen stroke of constant width along a polyline (a simple polygon)."""
    half = width / 2.0
    n = len(polyline)
    normals: list[Point] = []
    for i in range(n):
        a = polyline[max(0, i - 1)]
        b = polyline[min(n - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        normals.append((-dy / length, dx / length))
    left = [(x + nx * half, y + ny * half) for (x, y), (nx, ny) in zip(polyline, normals)]
    right = [(x - nx * half, y - ny * half) for (x, y), (nx, ny) in zip(polyline, normals)]
    # round-ish caps: add the end points pushed along the direction
    return left + right[::-1]


def arc(cx: float, cy: float, r: float, a0: float, a1: float, n: int = 16) -> list[Point]:
    """Polyline along a circular arc from angle a0 to a1 (radians, clockwise on screen)."""
    return [(cx + r * math.cos(a0 + (a1 - a0) * k / n), cy + r * math.sin(a0 + (a1 - a0) * k / n)) for k in range(n + 1)]


def annulus(cx: float, cy: float, r_outer: float, r_inner: float, n: int = 32) -> list[list[Point]]:
    """A closed 'o' stroke: outer ring one way round, hole ring the other (nonzero fill)."""
    outer = [(cx + r_outer * math.cos(2 * math.pi * k / n), cy + r_outer * math.sin(2 * math.pi * k / n)) for k in range(n)]
    inner = [(cx + r_inner * math.cos(-2 * math.pi * k / n), cy + r_inner * math.sin(-2 * math.pi * k / n)) for k in range(n)]
    return [outer, inner]


@dataclass
class Glyph:
    """One synthetic handwritten unit: its pen strokes (each a list of rings) and center x."""

    label: str
    strokes: list[list[list[Point]]]
    center_x: float
    bbox: tuple[float, float, float, float] = field(default=(0, 0, 0, 0))

    def __post_init__(self) -> None:
        pts = [p for s in self.strokes for r in s for p in r]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))


def make_glyph(label: str, x: float, baseline: float, xh: float, pen: float = 4.0) -> Glyph:
    """Synthesize a crude but distinct outline-stroke glyph for a few lowercase letters."""
    asc = baseline - 1.5 * xh  # ascender top
    top = baseline - xh        # x-height line
    w = 0.6 * xh
    strokes: list[list[list[Point]]]
    if label == "l":
        strokes = [[sweep([(x + w / 2, asc), (x + w / 2, baseline)], pen)]]
    elif label == "i":
        strokes = [
            [sweep([(x + w / 2, top), (x + w / 2, baseline)], pen)],
            [annulus(x + w / 2, top - 0.3 * xh, pen * 0.6, 0.1)[0]],  # the dot: a tiny disc
        ]
    elif label == "t":
        strokes = [
            [sweep([(x + w / 2, asc + 0.2 * xh), (x + w / 2, baseline)], pen)],
            [sweep([(x, top), (x + w, top)], pen)],
        ]
    elif label == "o":
        strokes = [annulus(x + w / 2, baseline - xh / 2, xh / 2, xh / 2 - pen)]
    elif label == "c":
        strokes = [[sweep(arc(x + w / 2, baseline - xh / 2, xh / 2 - pen / 2, 0.3 * math.pi, 1.7 * math.pi), pen)]]
    elif label == "a":
        strokes = [
            annulus(x + w / 2, baseline - xh / 2, xh / 2, xh / 2 - pen),
            [sweep([(x + w, top), (x + w, baseline)], pen)],
        ]
    elif label == "h":
        strokes = [
            [sweep([(x, asc), (x, baseline)], pen)],
            [sweep(arc(x + w / 2, top + xh / 2 - pen, xh / 2 - pen, math.pi, 2 * math.pi) + [(x + w - pen, baseline)], pen)],
        ]
    else:  # a generic wavy stroke
        pts = [(x + w * k / 8, baseline - xh / 2 + (xh / 3) * math.sin(k)) for k in range(9)]
        strokes = [[sweep(pts, pen)]]
    return Glyph(label=label, strokes=strokes, center_x=x + w / 2)


def layout_text(text: str, x0: float, baseline: float, xh: float, pen: float = 4.0) -> list[Glyph]:
    """Lay out `text` left-to-right; spaces advance without a glyph."""
    glyphs: list[Glyph] = []
    x = x0
    for ch in text:
        if ch.isspace():
            x += 0.7 * xh
            continue
        glyphs.append(make_glyph(ch, x, baseline, xh, pen))
        x += 1.0 * xh
    return glyphs


# --------------------------------------------------------------------------- svg writer


def _d(rings: list[list[Point]]) -> str:
    return " ".join(
        "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in ring) + " Z" for ring in rings
    )


def png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def write_export(
    path: str | Path,
    page_size: tuple[int, int],
    strokes: list[list[list[Point]]],
    template: Image.Image | None = None,
    pad: tuple[int, int] = (0, 0),
    ink_offset: tuple[float, float] = (0.0, 0.0),
    extra: str = "",
    fill_rule: str | None = None,
) -> Path:
    """Write a reMarkable-shaped export.

    The export page is `page_size + pad` with a centered-x viewBox. Ink (page-px) is
    shifted by `ink_offset` into export space; the template (page-sized) is pasted into
    the export-sized raster at the same offset, so both layers agree like the device's do.
    """
    w, h = page_size
    ew, eh = w + pad[0], h + pad[1]
    half = ew / 2.0
    dx, dy = ink_offset

    image_el = ""
    if template is not None:
        canvas = Image.new("L", (ew, eh), 255)
        canvas.paste(template.convert("L"), (int(round(dx)), int(round(dy))))
        image_el = (
            f'<image x="{-half}" y="0" width="{ew}" height="{eh}" '
            f'xlink:href="data:image/png;base64,{png_b64(canvas)}"/>'
        )

    rule = f' fill-rule="{fill_rule}"' if fill_rule else ""
    paths = "".join(
        f'<path fill="#000000"{rule} d="{_d([[(x + dx - half, y + dy) for x, y in ring] for ring in rings])}"/>'
        for rings in strokes
    )
    svg = (
        f'<svg width="{ew}" height="{eh}" viewBox="{-half} 0 {ew} {eh}" '
        f'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        f"{image_el}{extra}{paths}</svg>"
    )
    path = Path(path)
    path.write_text(svg, encoding="utf-8")
    return path

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from svgpathtools import svg2paths2

# how many points to sample along each path segment
_SAMPLES_PER_SEG = 8


def _parse_color_luminance(stroke: str | None) -> float:
    """Relative luminance 0 (black) .. 1 (white) for an SVG stroke color; white if unknown."""
    if not stroke:
        return 1.0
    s = stroke.strip().lower()
    if s in ("none", "transparent"):
        return 1.0
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) == 6:
            r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
            return 0.2126 * r + 0.7152 * g + 0.0722 * b
    if s == "black":
        return 0.0
    if s == "white":
        return 1.0
    return 0.5  # unknown named color — neither clearly ink nor clearly template


@dataclass
class RawStroke:
    """One parsed SVG path: ordered points (SVG coords) plus its stroke luminance."""

    points: list[tuple[float, float]]
    luminance: float

    def is_dark(self, threshold: float = 0.5) -> bool:
        return self.luminance < threshold


def parse_svg_strokes(svg_path: str | Path) -> list[RawStroke]:
    """Parse an SVG export into per-path strokes (ordered points + stroke luminance)."""
    paths, attributes, _ = svg2paths2(str(svg_path))
    strokes: list[RawStroke] = []
    for path, attrs in zip(paths, attributes):
        pts: list[tuple[float, float]] = []
        for seg in path:
            for i in range(_SAMPLES_PER_SEG + 1):
                t = i / _SAMPLES_PER_SEG
                p = seg.point(t)
                pt = (float(p.real), float(p.imag))
                if not pts or pt != pts[-1]:
                    pts.append(pt)
        if len(pts) >= 2:
            strokes.append(RawStroke(points=pts, luminance=_parse_color_luminance(attrs.get("stroke"))))
    return strokes


# template rules/prompts render light gray (~0.6-0.8); ink is near-black.
_INK_LUMINANCE_THRESHOLD = 0.5


def separate_ink(strokes: list[RawStroke], threshold: float = _INK_LUMINANCE_THRESHOLD) -> list[RawStroke]:
    """Keep only the writer's ink strokes, dropping the printed template by luminance."""
    return [s for s in strokes if s.is_dark(threshold)]

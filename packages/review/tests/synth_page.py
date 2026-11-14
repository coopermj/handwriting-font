"""Deterministic synthetic capture page for review tests.

Writes a Contract X sidecar, a page raster and a CandidateSet directory in the
exact manifest format `ingest_segment.candidates_out` produces (candidates.json,
strokes/<id>.json, crop/<id>.png, lowest-confidence-first). Does not import
ingest_segment: the bits it needs (derive_context, crop origin truncation) are
copied locally.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hwfont_schema import (
    BBox,
    Candidate,
    CandidateProvenance,
    CandidateSet,
    CandidateStatus,
    CaptureSidecar,
    Context,
    Contour,
    Kind,
    Page,
    PositionInWord,
    Region,
    StrokeData,
    StrokePoint,
)
from PIL import Image, ImageDraw

ROW_H = 70
ROW_PITCH = 150
PAGE_W = 1000
PAGE_H = 1400
MARGIN = 80
UNIT_GAP_PX = 12
WORD_GAP_PX = 30
BOX_PAD_PX = 3
INK_WIDTH_PX = 3

CREATED_AT = "2026-11-14T00:00:00Z"
MODEL = "synthetic"
ALIGNMENT_METHOD = "fiducial"

Polyline = list[tuple[float, float]]

# ---------------------------------------------------------------- alphabet (em, y-up, baseline 0)

X_HEIGHT = 0.45
ASCENDER = 0.70
DESCENDER = -0.20


def _loop(cx: float = 0.2) -> Polyline:
    """Closed 16-gon centred (cx, 0.225), radii (0.18, 0.225): top vertex exactly 0.45, bottom 0."""
    pts = [
        (cx + 0.18 * math.cos(2 * math.pi * k / 16), 0.225 + 0.225 * math.sin(2 * math.pi * k / 16))
        for k in range(16)
    ]
    return pts + [pts[0]]


def _stem(x: float, top: float = X_HEIGHT, bottom: float = 0.0) -> Polyline:
    return [(x, bottom), (x + 0.02, top)]


_STUB: list[Polyline] = [[(0.0, 0.0), (0.02, 0.30)]]

ALPHABET: dict[str, list[Polyline]] = {
    # x-height
    "a": [_loop(), _stem(0.38)],
    "c": [_loop()[3:14]],
    "e": [_loop(), [(0.02, 0.225), (0.38, 0.225)]],
    "i": [_stem(0.0)],
    "m": [_stem(0.0), _loop(0.22), _loop(0.58)],
    "n": [_stem(0.0), _loop(0.22)],
    "o": [_loop()],
    "r": [_stem(0.0), [(0.02, 0.35), (0.20, 0.45)]],
    "s": [_loop()[2:10]],
    "u": [_loop(), _stem(0.38)],
    "v": [[(0.0, 0.45), (0.15, 0.0), (0.30, 0.45)]],
    "w": [[(0.0, 0.45), (0.1, 0.0), (0.2, 0.45), (0.3, 0.0), (0.4, 0.45)]],
    "x": [[(0.0, 0.0), (0.30, 0.45)], [(0.0, 0.45), (0.30, 0.0)]],
    "z": [[(0.0, 0.45), (0.30, 0.45), (0.0, 0.0), (0.30, 0.0)]],
    # ascenders
    "b": [_stem(0.0, ASCENDER), _loop(0.20)],
    "d": [_loop(), _stem(0.38, ASCENDER)],
    "f": [_stem(0.08, ASCENDER), [(0.0, 0.45), (0.20, 0.45)]],
    "h": [_stem(0.0, ASCENDER), _loop(0.22)],
    "k": [_stem(0.0, ASCENDER), [(0.30, 0.45), (0.02, 0.20), (0.30, 0.0)]],
    "l": [_stem(0.0, ASCENDER)],
    "t": [_stem(0.08, ASCENDER), [(0.0, 0.45), (0.20, 0.45)]],
    # descenders
    "g": [_loop(), _stem(0.38, X_HEIGHT, DESCENDER)],
    "j": [_stem(0.0, X_HEIGHT, DESCENDER)],
    "p": [_stem(0.0, X_HEIGHT, DESCENDER), _loop(0.20)],
    "q": [_loop(), _stem(0.38, X_HEIGHT, DESCENDER)],
    "y": [[(0.0, 0.45), (0.15, 0.0)], _stem(0.28, X_HEIGHT, DESCENDER)],
    # punctuation
    ".": [[(0.05, 0.02), (0.053, 0.023)]],
}


def glyph(ch: str) -> list[Polyline]:
    """Em-space polylines for one character (capital, digit/punctuation stub, or loop fallback)."""
    if ch in ALPHABET:
        return ALPHABET[ch]
    if ch.isupper():
        return [_stem(0.0, ASCENDER), [(0.0, ASCENDER), (0.30, ASCENDER)]]
    if ch.isdigit() or not ch.isalpha():
        return _STUB
    return [_loop()]


# ---------------------------------------------------------------- context (local copy of ingest_segment.segment)


def _unit_map(transcript: str) -> list[tuple[str, int, int, int]]:
    """For each non-space char (in order): (char, word_index, pos_in_word, word_len)."""
    out: list[tuple[str, int, int, int]] = []
    word_index = -1
    pos_in_word = 0
    prev_space = True
    lengths = [len(w) for w in transcript.split()]
    for ch in transcript:
        if ch.isspace():
            prev_space = True
            continue
        if prev_space:
            word_index += 1
            pos_in_word = 0
            prev_space = False
        out.append((ch, word_index, pos_in_word, lengths[word_index]))
        pos_in_word += 1
    return out


def derive_context(transcript: str, label: str, unit_index: int) -> Context:
    """Copy of ingest_segment.segment.derive_context."""
    units = _unit_map(transcript)
    if unit_index < 0 or unit_index >= len(units):
        return Context(source_word=label, position_in_word=PositionInWord.isolated)

    _, word_index, pos, word_len = units[unit_index]
    source_word = transcript.split()[word_index]

    if word_len == 1:
        position = PositionInWord.isolated
    elif pos == 0:
        position = PositionInWord.initial
    elif pos == word_len - 1:
        position = PositionInWord.final
    else:
        position = PositionInWord.medial

    left = units[unit_index - 1] if pos > 0 else None
    right = units[unit_index + 1] if pos < word_len - 1 else None
    return Context(
        source_word=source_word,
        left_neighbor=left[0] if left else None,
        right_neighbor=right[0] if right else None,
        position_in_word=position,
    )


def ligature_context(transcript: str, start: int, n: int) -> Context:
    """Context for a candidate covering units start..start+n-1 (n == 1 is derive_context)."""
    if n == 1:
        return derive_context(transcript, transcript.replace(" ", "")[start], start)
    units = _unit_map(transcript)
    _, word_index, pos, word_len = units[start]
    last_pos = pos + n - 1
    at_start, at_end = pos == 0, last_pos == word_len - 1
    if at_start and at_end:
        position = PositionInWord.isolated
    elif at_start:
        position = PositionInWord.initial
    elif at_end:
        position = PositionInWord.final
    else:
        position = PositionInWord.medial
    return Context(
        source_word=transcript.split()[word_index],
        left_neighbor=None if at_start else units[start - 1][0],
        right_neighbor=None if at_end else units[start + n][0],
        position_in_word=position,
    )


# ---------------------------------------------------------------- page


@dataclass
class SynthPage:
    sidecar_path: Path
    candidate_dir: Path
    raster_path: Path | None
    page: Page
    candidate_set: CandidateSet
    ink: dict[str, list[Polyline]]


def _extent_box(polys: list[Polyline]) -> BBox:
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    x0, y0 = min(xs) - BOX_PAD_PX, min(ys) - BOX_PAD_PX
    return BBox(x=x0, y=y0, w=max(xs) + BOX_PAD_PX - x0, h=max(ys) + BOX_PAD_PX - y0)


def _crop_png(raster: Image.Image, bbox: BBox) -> bytes:
    crop = raster.crop((int(bbox.x), int(bbox.y), int(bbox.x + bbox.w), int(bbox.y + bbox.h)))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


def _layout_units(transcript: str, bbox: BBox, baseline_y: float) -> list[list[Polyline]]:
    """Page-px polylines per non-space unit, laid out left to right."""
    out: list[list[Polyline]] = []
    cursor = bbox.x + 20
    for ch in transcript:
        if ch.isspace():
            cursor += WORD_GAP_PX
            continue
        em = glyph(ch)
        polys = [[(cursor + x * ROW_H, baseline_y - y * ROW_H) for x, y in p] for p in em]
        xs = [x for p in polys for x, _ in p]
        out.append(polys)
        cursor += (max(xs) - min(xs)) + UNIT_GAP_PX
    return out


def synth_page(
    out_dir: Path,
    transcripts: list[str],
    *,
    seed: int = 1,
    ligatures: dict[int, list[tuple[int, str]]] | None = None,
    raster: bool = True,
    page_id: str = "p0",
    session_stem: str = "capture",
    contexts: Literal["transcript", "ingest"] = "transcript",
) -> SynthPage:
    """`contexts="transcript"` gives every candidate its true Context (cumulative character offsets);
    `"ingest"` reproduces the pre-fix ingest, `derive_context(transcript, label, box_index)`, so every box
    after a ligature box carries a Context shifted by `len(label) - 1` units."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ligatures = ligatures or {}
    rng = random.Random(seed)

    img = Image.new("L", (PAGE_W, PAGE_H), 255)
    draw = ImageDraw.Draw(img)

    regions: list[Region] = []
    # (candidate, page-px polylines) in transcript order
    items: list[tuple[Candidate, list[Polyline]]] = []

    for ri, transcript in enumerate(transcripts):
        bbox = BBox(x=MARGIN, y=MARGIN + ri * ROW_PITCH, w=PAGE_W - 2 * MARGIN, h=ROW_H)
        baseline_y = bbox.y + ROW_H
        units = [ch for ch in transcript if not ch.isspace()]
        ligs = dict(ligatures.get(ri, []))
        for start, label in ligs.items():
            if "".join(units[start : start + len(label)]) != label:
                raise ValueError(f"ligature {label!r} does not match units at {start} in {transcript!r}")
        region = Region(
            id=f"{page_id}-r{ri}",
            expected_transcript=transcript,
            baseline_y=baseline_y,
            bbox=bbox,
            expected_units=units,
            ligature_targets=list(dict.fromkeys(ligs.values())),
        )
        regions.append(region)

        unit_polys = _layout_units(transcript, bbox, baseline_y)
        for polys in unit_polys:
            for p in polys:
                draw.line(p, fill=0, width=INK_WIDTH_PX)

        # group units into candidates
        groups: list[tuple[int, int, str]] = []  # (start, n, label)
        u = 0
        while u < len(units):
            if u in ligs:
                groups.append((u, len(ligs[u]), ligs[u]))
                u += len(ligs[u])
            else:
                groups.append((u, 1, units[u]))
                u += 1
        count_mismatch = len(groups) != len(units)

        for ci, (start, n, label) in enumerate(groups):
            polys = [p for k in range(start, start + n) for p in unit_polys[k]]
            confidence = rng.uniform(0.6, 1.0)
            needs_review = count_mismatch
            if not items:  # first candidate of the page
                confidence, needs_review = 0.3, True
            cand = Candidate(
                id=f"{region.id}-{ci}",
                page_id=page_id,
                region_id=region.id,
                label=label,
                kind=Kind.ligature if n > 1 else Kind.single,
                confidence=confidence,
                bbox=_extent_box(polys),
                context=(
                    ligature_context(transcript, start, n)
                    if contexts == "transcript"
                    else derive_context(transcript, label, ci)
                ),
                status=CandidateStatus.needs_review if needs_review else CandidateStatus.pending,
                alignment_method=ALIGNMENT_METHOD,
                model=MODEL,
                created_at=CREATED_AT,
            )
            items.append((cand, polys))

    page = Page(id=page_id, width_px=PAGE_W, height_px=PAGE_H, dpi=226, fiducials=[], regions=regions)
    sidecar_path = out_dir / f"{session_stem}.sidecar.json"
    sidecar_path.write_text(CaptureSidecar(pages=[page]).model_dump_json(), encoding="utf-8")

    raster_path: Path | None = None
    if raster:
        raster_path = out_dir / "page.png"
        img.save(raster_path)

    # manifest, same layout as ingest_segment.candidates_out.write_candidate_set
    candidate_dir = out_dir / f"{session_stem}-{page_id}-candidates"
    (candidate_dir / "strokes").mkdir(parents=True, exist_ok=True)
    (candidate_dir / "crop").mkdir(parents=True, exist_ok=True)
    stored: list[Candidate] = []
    ink: dict[str, list[Polyline]] = {}
    for cand, polys in items:
        strokes_rel = f"strokes/{cand.id}.json"
        crop_rel = f"crop/{cand.id}.png"
        data = StrokeData(
            contours=[Contour(points=[StrokePoint(x=x, y=y) for x, y in p]) for p in polys]
        )
        (candidate_dir / strokes_rel).write_text(data.model_dump_json(), encoding="utf-8")
        (candidate_dir / crop_rel).write_bytes(_crop_png(img, cand.bbox))
        stored.append(cand.model_copy(update={"strokes_path": strokes_rel, "crop_path": crop_rel}))
        ink[cand.id] = polys
    stored.sort(key=lambda c: c.confidence)
    cs = CandidateSet(
        provenance=CandidateProvenance(
            source_page_id=page_id,
            source_raster="page.png",
            alignment_method=ALIGNMENT_METHOD,
            model=MODEL,
        ),
        candidates=stored,
    )
    (candidate_dir / "candidates.json").write_text(cs.model_dump_json(), encoding="utf-8")

    return SynthPage(
        sidecar_path=sidecar_path,
        candidate_dir=candidate_dir,
        raster_path=raster_path,
        page=page,
        candidate_set=cs,
        ink=ink,
    )

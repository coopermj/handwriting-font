from __future__ import annotations

import base64
import io
import statistics
from typing import Callable

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from hwfont_schema import (
    BBox,
    Candidate,
    CandidateStatus,
    Context,
    Kind,
    PositionInWord,
    Region,
)
from ingest_segment.remarkable_svg import PenStroke

VISION_MODEL = "claude-opus-4-8"


def _unit_map(transcript: str) -> list[tuple[str, int, int, int]]:
    """For each non-space char (in order): (char, word_index, pos_in_word, word_len)."""
    out: list[tuple[str, int, int, int]] = []
    word_index = -1
    pos_in_word = 0
    prev_space = True
    # precompute word lengths (non-space run lengths)
    words = transcript.split()
    lengths = [len(w) for w in words]
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
    """Build a Context for the unit at `unit_index` from the known transcript.

    Falls back to an isolated, neighborless context if the index is out of range
    (caller flags the candidate needs_review in that case).
    """
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


# --------------------------------------------------------------------------- vision contract


class StrokeSummary(BaseModel):
    """One pen stroke as presented to the vision model: its id and crop-pixel bbox."""

    id: int
    x: float
    y: float
    w: float
    h: float


class VisionBox(BaseModel):
    """One labeled unit located by the vision model, in crop pixel coordinates."""

    label: str
    kind: str  # "single" | "ligature"
    x: float
    y: float
    w: float = Field(gt=0)
    h: float = Field(gt=0)
    confidence: float = Field(ge=0.0, le=1.0)


class VisionUnit(BaseModel):
    """One labeled unit as a group of stroke ids (the stroke-grouping protocol)."""

    label: str
    kind: str  # "single" | "ligature"
    stroke_ids: list[int] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class VisionResult(BaseModel):
    """The structured vision response, ordered left-to-right.

    Exactly one of `units` (stroke grouping, when the region had strokes) or `boxes`
    (pixel boxes, raster-only pages) is populated.
    """

    boxes: list[VisionBox] = Field(default_factory=list)
    units: list[VisionUnit] = Field(default_factory=list)


class _BoxResponse(BaseModel):
    boxes: list[VisionBox] = Field(default_factory=list)


class _UnitResponse(BaseModel):
    units: list[VisionUnit] = Field(default_factory=list)


VisionFn = Callable[[bytes, Region, list[StrokeSummary]], VisionResult]


def suggest_groups(strokes: list[StrokeSummary]) -> list[list[int]]:
    """Deterministic left-to-right gap clustering of strokes (advisory hint for vision).

    Strokes are sorted by left edge; a new group starts where a stroke begins more than
    a quarter of the median stroke height past the current group's right edge.
    """
    if not strokes:
        return []
    ordered = sorted(strokes, key=lambda s: s.x)
    gap = max(3.0, 0.25 * statistics.median(s.h for s in ordered))
    groups: list[list[int]] = [[ordered[0].id]]
    right = ordered[0].x + ordered[0].w
    for s in ordered[1:]:
        if s.x > right + gap:
            groups.append([s.id])
        else:
            groups[-1].append(s.id)
        right = max(right, s.x + s.w)
    return groups


def build_region_prompt(transcript: str, expected_units: list[str]) -> str:
    """Prompt for one region (pixel-box protocol): the writer copied a known line; locate each unit."""
    units = " ".join(expected_units)
    return (
        "This image crop is one ruled row from a handwriting-capture page. "
        f"The writer was asked to copy this exact text: {transcript!r}\n"
        f"Expected units to locate, in order: {units}\n"
        "Locate and label each handwritten unit you were asked to capture. "
        "Return boxes in crop pixel coordinates (origin top-left), ordered left-to-right. "
        "Use kind 'single' for one glyph and 'ligature' for a multi-character cluster. "
        "Set confidence in [0,1] reflecting how clearly you can locate the unit."
    )


def build_grouping_prompt(transcript: str, expected_units: list[str], strokes: list[StrokeSummary]) -> str:
    """Prompt for one region (stroke-grouping protocol): assign every numbered stroke to a unit."""
    units = " ".join(expected_units)
    listing = "\n".join(
        f"  stroke {s.id}: x={s.x:.0f} y={s.y:.0f} w={s.w:.0f} h={s.h:.0f}" for s in strokes
    )
    hint = suggest_groups(strokes)
    return (
        "This image crop is one ruled row from a handwriting-capture page. "
        f"The writer was asked to copy this exact text: {transcript!r}\n"
        "Each pen stroke is drawn in its own color with its numeric id written just above "
        "its top-left corner. The strokes, with bounding boxes in crop pixels (origin top-left):\n"
        f"{listing}\n"
        f"A left-to-right gap heuristic suggests these groups (advisory only): {hint}\n"
        f"Expected units to capture, in order: {units}\n"
        "Group the strokes into the handwritten units. Assign every stroke id to exactly one "
        "unit (an i-dot or t-bar belongs with its letter; a connected cursive cluster is one "
        "'ligature' unit). Return units ordered left-to-right with their stroke ids. "
        "Use kind 'single' for one glyph and 'ligature' for a multi-character cluster. "
        "Set confidence in [0,1] reflecting how sure you are of the grouping and label."
    )


class ClaudeVisionClient:
    """Wraps the Anthropic SDK to return a validated VisionResult for a region crop."""

    def __init__(self, client, model: str = VISION_MODEL) -> None:
        self._client = client
        self.model = model

    def __call__(self, crop_png: bytes, region: Region, strokes: list[StrokeSummary] = ()) -> VisionResult:
        strokes = list(strokes)
        if strokes:
            prompt = build_grouping_prompt(region.expected_transcript, region.expected_units, strokes)
            output_format: type[BaseModel] = _UnitResponse
        else:
            prompt = build_region_prompt(region.expected_transcript, region.expected_units)
            output_format = _BoxResponse
        b64 = base64.standard_b64encode(crop_png).decode("ascii")
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            output_format=output_format,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": b64},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        parsed = response.parsed_output
        if isinstance(parsed, _UnitResponse):
            return VisionResult(units=parsed.units)
        return VisionResult(boxes=parsed.boxes)


# --------------------------------------------------------------------------- segmentation


# a box's confidence below this flags its candidate needs_review
_LOW_CONFIDENCE = 0.5
# a stroke belongs to a region/box when at least this fraction of its points fall inside
_STROKE_INSIDE_FRACTION = 0.5
# padding (px) around a stroke-derived candidate bbox so the crop shows the ink edge
_BBOX_PAD = 2.0

# distinct colors for numbered strokes in the annotated crop handed to vision
_PALETTE = [
    (200, 0, 0), (0, 120, 0), (0, 0, 220), (190, 100, 0), (120, 0, 160),
    (0, 140, 140), (160, 20, 90), (90, 90, 0), (0, 60, 200), (220, 60, 120),
]


def _crop_png(raster: Image.Image, bbox: BBox) -> bytes:
    left, top = int(bbox.x), int(bbox.y)
    right, bottom = int(bbox.x + bbox.w), int(bbox.y + bbox.h)
    crop = raster.crop((left, top, right, bottom))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


def _font(size: int = 12) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # older Pillow: no size parameter
        return ImageFont.load_default()


def _annotated_crop_png(raster: Image.Image, bbox: BBox, strokes: list[PenStroke]) -> bytes:
    """Region crop with each stroke drawn in its own color and numbered (for grouping)."""
    left, top = int(bbox.x), int(bbox.y)
    crop = raster.crop((left, top, int(bbox.x + bbox.w), int(bbox.y + bbox.h))).convert("RGB")
    draw = ImageDraw.Draw(crop)
    font = _font(12)
    for i, stroke in enumerate(strokes):
        color = _PALETTE[i % len(_PALETTE)]
        for line, widths in zip(stroke.centerlines, stroke.widths):
            pts = [(x - left, y - top) for x, y in line]
            w = max(1, round(sum(widths) / len(widths)))
            if len(pts) >= 2:
                draw.line(pts, fill=color, width=w)
            else:
                draw.point(pts, fill=color)
        x0, y0, _, _ = stroke.bbox
        draw.text((x0 - left, max(0.0, y0 - top - 13)), str(i), fill=color, font=font)
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


def _points_in_box(points: list[tuple[float, float]], box: BBox) -> float:
    if not points:
        return 0.0
    inside = sum(
        1 for x, y in points if box.x <= x <= box.x + box.w and box.y <= y <= box.y + box.h
    )
    return inside / len(points)


def _stroke_summary(i: int, stroke: PenStroke, region: Region) -> StrokeSummary:
    x0, y0, x1, y1 = stroke.bbox
    return StrokeSummary(
        id=i, x=x0 - region.bbox.x, y=y0 - region.bbox.y, w=max(1.0, x1 - x0), h=max(1.0, y1 - y0)
    )


def _union_bbox(strokes: list[PenStroke]) -> BBox:
    boxes = [s.bbox for s in strokes]
    x0 = min(b[0] for b in boxes) - _BBOX_PAD
    y0 = min(b[1] for b in boxes) - _BBOX_PAD
    x1 = max(b[2] for b in boxes) + _BBOX_PAD
    y1 = max(b[3] for b in boxes) + _BBOX_PAD
    return BBox(x=x0, y=y0, w=max(1.0, x1 - x0), h=max(1.0, y1 - y0))


def _nearest_index(boxes: list[BBox], x: float, y: float) -> int:
    def d(b: BBox) -> float:
        cx, cy = b.x + b.w / 2, b.y + b.h / 2
        return (cx - x) ** 2 + (cy - y) ** 2

    return min(range(len(boxes)), key=lambda i: d(boxes[i]))


def segment_region(
    region: Region,
    raster: Image.Image,
    page_strokes: list[PenStroke],
    vision: VisionFn,
    page_id: str,
    alignment_method: str,
    page_low_confidence: bool,
    model: str,
    created_at: str,
) -> list[tuple[Candidate, list[PenStroke]]]:
    """Locate, label, and box each unit in one region; map strokes; build candidates.

    `page_strokes` are aligned pen strokes in page-pixel space. When the region holds
    strokes, vision is asked to *group numbered strokes* into units (bboxes are then
    exact unions of the assigned ink); otherwise it returns pixel boxes and strokes are
    assigned by overlap. Returns (candidate, assigned_strokes) pairs; the caller writes
    stroke/crop files. Every anomaly becomes a review reason on the affected candidate,
    never a silent drop.
    """
    region_strokes = [
        s for s in page_strokes if _points_in_box(s.points, region.bbox) >= _STROKE_INSIDE_FRACTION
    ]
    summaries = [_stroke_summary(i, s, region) for i, s in enumerate(region_strokes)]

    if region_strokes:
        result = vision(_annotated_crop_png(raster, region.bbox, region_strokes), region, summaries)
    else:
        result = vision(_crop_png(raster, region.bbox), region, [])

    labels: list[tuple[str, str, float]] = []  # (label, kind, confidence)
    bboxes: list[BBox] = []
    assigned: list[list[PenStroke]] = []
    reasons: list[list[str]] = []
    owners: dict[int, list[int]] = {i: [] for i in range(len(region_strokes))}

    if result.units:
        for i, unit in enumerate(result.units):
            r: list[str] = []
            ids = []
            for sid in unit.stroke_ids:
                if sid in owners:
                    ids.append(sid)
                    owners[sid].append(i)
                else:
                    r.append("unknown_stroke_id")
            strokes = [region_strokes[sid] for sid in ids]
            if strokes:
                bbox = _union_bbox(strokes)
            else:
                r.append("no_strokes")
                bbox = region.bbox.model_copy()
            labels.append((unit.label, unit.kind, unit.confidence))
            bboxes.append(bbox)
            assigned.append(strokes)
            reasons.append(r)
    else:
        for i, box in enumerate(result.boxes):
            # crop px -> page px: offset by the region crop origin
            bbox = BBox(x=region.bbox.x + box.x, y=region.bbox.y + box.y, w=box.w, h=box.h)
            strokes = []
            for sid, s in enumerate(region_strokes):
                if _points_in_box(s.points, bbox) >= _STROKE_INSIDE_FRACTION:
                    strokes.append(s)
                    owners[sid].append(i)
            labels.append((box.label, box.kind, box.confidence))
            bboxes.append(bbox)
            assigned.append(strokes)
            reasons.append([])

    # cross-candidate stroke accounting: shared strokes and strokes nobody claimed
    for sid, cands in owners.items():
        if len(cands) > 1:
            for i in cands:
                reasons[i].append("stroke_shared")
        elif not cands and bboxes:
            x0, y0, x1, y1 = region_strokes[sid].bbox
            reasons[_nearest_index(bboxes, (x0 + x1) / 2, (y0 + y1) / 2)].append("unassigned_stroke_nearby")

    count_mismatch = len(labels) != len(region.expected_units)
    out: list[tuple[Candidate, list[PenStroke]]] = []
    for i, ((label, kind, confidence), bbox, strokes, r) in enumerate(zip(labels, bboxes, assigned, reasons)):
        if count_mismatch:
            r.append("count_mismatch")
        if page_low_confidence:
            r.append("page_low_confidence")
        if confidence < _LOW_CONFIDENCE:
            r.append("low_confidence")
        if any(s.low_coverage for s in strokes):
            r.append("low_coverage_stroke")
        r = list(dict.fromkeys(r))  # dedupe, keep order
        status = CandidateStatus.needs_review if r else CandidateStatus.pending
        candidate = Candidate(
            id=f"{region.id}-{i}",
            page_id=page_id,
            region_id=region.id,
            label=label,
            kind=Kind(kind),
            confidence=confidence,
            bbox=bbox,
            context=derive_context(region.expected_transcript, label, i),
            status=status,
            review_reasons=r,
            alignment_method=alignment_method,
            model=model,
            created_at=created_at,
        )
        out.append((candidate, strokes))
    return out

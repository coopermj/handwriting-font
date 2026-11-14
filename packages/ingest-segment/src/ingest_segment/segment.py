from __future__ import annotations

import base64
import io
from typing import Callable

from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from hwfont_schema import (
    BBox,
    Candidate,
    CandidateStatus,
    Context,
    Kind,
    PositionInWord,
    Region,
)

VISION_MODEL = "claude-opus-4-8"
# Output cap for one region's VisionResult. Adaptive thinking shares this budget, and a
# real 60-unit row produced ~4.5k characters of boxes alone, which 4096 truncated
# mid-JSON (2026-11-14). 16000 is the guideline ceiling for a non-streaming request
# (the SDK requires streaming well above it); the SDK itself has no default.
VISION_MAX_TOKENS = 16000


class VisionError(RuntimeError):
    """The vision call returned no usable VisionResult for a region (refusal, no text block)."""


class VisionTruncated(VisionError):
    """The vision response hit `max_tokens` before the box list was complete."""


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


def derive_context(transcript: str, label: str, unit_index: int, length: int = 1) -> Context:
    """Build a Context for the span of `length` units starting at `unit_index` from the known transcript.

    `unit_index` is the cumulative character offset of the span (not a box index); a multi-character
    label (ligature) covers `length` units, its right neighbour is the unit after its last character and
    its position is initial/final/isolated/medial for the whole span within the first unit's word.
    Falls back to an isolated, neighborless context if the index is out of range
    (caller flags the candidate needs_review in that case).
    """
    units = _unit_map(transcript)
    if unit_index < 0 or unit_index >= len(units):
        return Context(source_word=label, position_in_word=PositionInWord.isolated)

    _, word_index, pos, word_len = units[unit_index]
    source_word = transcript.split()[word_index]
    last_pos = pos + max(length, 1) - 1
    at_start = pos == 0
    at_end = last_pos >= word_len - 1

    if at_start and at_end:
        position = PositionInWord.isolated
    elif at_start:
        position = PositionInWord.initial
    elif at_end:
        position = PositionInWord.final
    else:
        position = PositionInWord.medial

    left = units[unit_index - 1] if not at_start else None
    right_index = unit_index + max(length, 1)
    right = units[right_index] if not at_end and right_index < len(units) else None
    return Context(
        source_word=source_word,
        left_neighbor=left[0] if left else None,
        right_neighbor=right[0] if right else None,
        position_in_word=position,
    )


class VisionBox(BaseModel):
    """One labeled unit located by the vision model, in crop pixel coordinates."""

    label: str
    kind: str  # "single" | "ligature"
    x: float
    y: float
    w: float = Field(gt=0)
    h: float = Field(gt=0)
    confidence: float = Field(ge=0.0, le=1.0)


class VisionResult(BaseModel):
    """The structured vision response: labeled boxes ordered left-to-right."""

    boxes: list[VisionBox] = Field(default_factory=list)


def build_region_prompt(transcript: str, expected_units: list[str]) -> str:
    """Prompt for one region: the writer copied a known line; locate each unit."""
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


class ClaudeVisionClient:
    """Wraps the Anthropic SDK to return a validated VisionResult for a region crop."""

    def __init__(self, client, model: str = VISION_MODEL) -> None:
        self._client = client
        self.model = model

    def __call__(self, crop_png: bytes, region: Region) -> VisionResult:
        b64 = base64.standard_b64encode(crop_png).decode("ascii")
        try:
            response = self._parse(b64, region)
        except ValidationError as e:
            # output_config.format constrains the grammar, so JSON that does not validate
            # is JSON that was cut off: the SDK validates inside parse(), before we can
            # read stop_reason.
            raise VisionTruncated(
                f"vision response for region {region.id!r} did not validate, most likely cut "
                f"off by max_tokens={VISION_MAX_TOKENS} ({len(region.expected_units)} expected units)"
            ) from e
        if response.stop_reason == "max_tokens":
            raise VisionTruncated(
                f"vision response for region {region.id!r} hit max_tokens={VISION_MAX_TOKENS}; "
                f"{len(region.expected_units)} expected units"
            )
        if response.parsed_output is None:
            raise VisionError(
                f"vision response for region {region.id!r} carried no boxes "
                f"(stop_reason={response.stop_reason!r})"
            )
        return response.parsed_output

    def _parse(self, b64: str, region: Region):
        return self._client.messages.parse(
            model=self.model,
            max_tokens=VISION_MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            output_format=VisionResult,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": b64},
                        },
                        {
                            "type": "text",
                            "text": build_region_prompt(region.expected_transcript, region.expected_units),
                        },
                    ],
                }
            ],
        )


# a box's confidence below this flags its candidate needs_review
_LOW_CONFIDENCE = 0.5
# a stroke is assigned to a box if at least this fraction of its points fall inside.
# NOTE: this is a simplified, independent-per-box rule (a straddling stroke can be
# assigned to more than one box, and unassigned strokes are not separately flagged) —
# not the design spec's single majority-overlap-box rule. Acceptable for the expected
# non-overlapping box layout; revisit against a real device export.
_STROKE_INSIDE_FRACTION = 0.5

VisionFn = Callable[[bytes, Region], VisionResult]


def _crop_png(raster: Image.Image, bbox: BBox) -> bytes:
    left, top = int(bbox.x), int(bbox.y)
    right, bottom = int(bbox.x + bbox.w), int(bbox.y + bbox.h)
    crop = raster.crop((left, top, right, bottom))
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


def segment_region(
    region: Region,
    raster: Image.Image,
    page_strokes: list[list[tuple[float, float]]],
    vision: VisionFn,
    page_id: str,
    alignment_method: str,
    page_low_confidence: bool,
    model: str,
    created_at: str,
) -> list[tuple[Candidate, list[list[tuple[float, float]]]]]:
    """Locate, label, and box each unit in one region; map strokes; build candidates.

    `page_strokes` are aligned ink strokes in page-pixel space. Returns
    (candidate, assigned_strokes) pairs; the caller writes stroke/crop files.
    """
    crop = _crop_png(raster, region.bbox)
    result = vision(crop, region)

    count_mismatch = len(result.boxes) != len(region.expected_units)

    out: list[tuple[Candidate, list[list[tuple[float, float]]]]] = []
    offset = 0  # cumulative character offset: a ligature box covers len(label) units
    for i, box in enumerate(result.boxes):
        # crop px -> page px: offset by the region crop origin
        page_bbox = BBox(
            x=region.bbox.x + box.x,
            y=region.bbox.y + box.y,
            w=box.w,
            h=box.h,
        )
        assigned = [
            s for s in page_strokes if _points_in_box(s, page_bbox) >= _STROKE_INSIDE_FRACTION
        ]
        context = derive_context(region.expected_transcript, box.label, offset, len(box.label))
        offset += len(box.label)

        needs_review = (
            count_mismatch
            or page_low_confidence
            or box.confidence < _LOW_CONFIDENCE
        )
        status = CandidateStatus.needs_review if needs_review else CandidateStatus.pending

        candidate = Candidate(
            id=f"{region.id}-{i}",
            page_id=page_id,
            region_id=region.id,
            label=box.label,
            kind=Kind(box.kind),
            confidence=box.confidence,
            bbox=page_bbox,
            context=context,
            status=status,
            alignment_method=alignment_method,
            model=model,
            created_at=created_at,
        )
        out.append((candidate, assigned))
    return out

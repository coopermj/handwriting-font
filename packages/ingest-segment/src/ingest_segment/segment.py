from __future__ import annotations

import base64

from pydantic import BaseModel, Field

from hwfont_schema import Context, PositionInWord, Region

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
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=4096,
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
        return response.parsed_output

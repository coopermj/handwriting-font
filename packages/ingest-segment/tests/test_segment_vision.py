import pytest
from hwfont_schema import BBox, Region

from ingest_segment.segment import (
    VISION_MAX_TOKENS,
    ClaudeVisionClient,
    VisionBox,
    VisionError,
    VisionResult,
    VisionTruncated,
    build_region_prompt,
)


def test_vision_result_schema_validates():
    vr = VisionResult(
        boxes=[
            VisionBox(label="c", kind="single", x=0, y=0, w=10, h=20, confidence=0.9),
            VisionBox(label="at", kind="ligature", x=10, y=0, w=18, h=20, confidence=0.4),
        ]
    )
    assert VisionResult.model_validate_json(vr.model_dump_json()) == vr


def test_prompt_includes_transcript_and_units():
    prompt = build_region_prompt(transcript="the cat", expected_units=["t", "h", "e", "c", "a", "t"])
    assert "the cat" in prompt
    assert "crop pixel coordinates" in prompt.lower()


_ONE_BOX_JSON = '{"boxes":[{"label":"a","kind":"single","x":0,"y":0,"w":5,"h":5,"confidence":1.0}]}'


class _FakeMessages:
    """Mimics anthropic's Messages.parse: it validates the text block *inside* parse(),
    so a cut-off body raises pydantic's ValidationError there, and a response with no
    text block (thinking-only truncation, refusal) has parsed_output None."""

    def __init__(self, stop_reason="end_turn", text=_ONE_BOX_JSON):
        self.stop_reason = stop_reason
        self.text = text
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        parsed = VisionResult.model_validate_json(self.text) if self.text is not None else None
        return type("Resp", (), {"parsed_output": parsed, "stop_reason": self.stop_reason})()


class _FakeClient:
    def __init__(self, stop_reason="end_turn", text=_ONE_BOX_JSON):
        self.messages = _FakeMessages(stop_reason, text)


def _region():
    return Region(
        id="p0-r0", expected_transcript="a", expected_units=["a"], ligature_targets=[],
        bbox=BBox(x=0, y=0, w=10, h=10), baseline_y=8.0,
    )


def test_vision_client_gives_the_box_list_room_to_finish():
    client = _FakeClient()
    result = ClaudeVisionClient(client)(b"png", _region())
    assert result.boxes[0].label == "a"
    assert client.messages.kwargs["max_tokens"] == VISION_MAX_TOKENS
    assert VISION_MAX_TOKENS >= 16000  # a 60-unit row needs well over 4096 with thinking


def test_vision_client_maps_a_cut_off_json_body_to_truncated():
    # the real failure (2026-11-14): JSON cut mid-string, ValidationError raised inside parse()
    cut = '{"boxes":[{"label":"a","kind":"single","x":0,"y":0,"w":5,"h":5,"confi'
    with pytest.raises(VisionTruncated, match="p0-r0"):
        ClaudeVisionClient(_FakeClient("max_tokens", text=cut))(b"png", _region())


def test_vision_client_raises_when_thinking_alone_exhausted_the_budget():
    with pytest.raises(VisionTruncated, match="max_tokens"):
        ClaudeVisionClient(_FakeClient("max_tokens", text=None))(b"png", _region())


def test_vision_client_raises_on_a_refusal_instead_of_returning_none():
    with pytest.raises(VisionError, match="refusal"):
        ClaudeVisionClient(_FakeClient("refusal", text=None))(b"png", _region())

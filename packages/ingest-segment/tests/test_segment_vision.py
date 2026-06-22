from ingest_segment.segment import VisionBox, VisionResult, build_region_prompt


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

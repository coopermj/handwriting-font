from pydantic import BaseModel, Field

from hwfont_schema.enums import Kind, PositionInWord, Quality, ReviewStatus
from hwfont_schema.geometry import BBox


class Context(BaseModel):
    """Where a sample came from — drives contextual alternates in font-gen."""

    source_word: str
    left_neighbor: str | None = None
    right_neighbor: str | None = None
    position_in_word: PositionInWord


class Metrics(BaseModel):
    """Em-normalized typographic metrics for one sample."""

    baseline: float
    x_height: float
    advance: float = Field(gt=0)
    bbox: BBox


class Sample(BaseModel):
    """Contract Y: one reviewed, accepted glyph/ligature sample. Ink lives in sidecar files."""

    id: str
    label: str
    kind: Kind
    strokes_path: str | None = None
    raster_path: str | None = None
    context: Context
    metrics: Metrics
    quality: Quality
    review_status: ReviewStatus
    capture_session_id: str
    created_at: str  # ISO-8601, supplied by the caller


class Target(BaseModel):
    """A glyph or ligature we want, with how many samples are required for good coverage."""

    label: str
    kind: Kind
    required_count: int = Field(ge=1)

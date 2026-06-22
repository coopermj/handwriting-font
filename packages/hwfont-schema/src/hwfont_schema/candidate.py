from pydantic import BaseModel, Field

from hwfont_schema.enums import CandidateStatus, Kind
from hwfont_schema.geometry import BBox
from hwfont_schema.sample import Context


class Candidate(BaseModel):
    """A proposed-but-unconfirmed glyph/ligature sample from the vision first pass.

    Geometry (`bbox`) is in Contract X page-pixel space, un-normalized. On accept
    in `review`, it is normalized to em-space and written as a `Sample`.
    """

    id: str
    page_id: str
    region_id: str
    label: str
    kind: Kind
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: BBox
    context: Context
    strokes_path: str | None = None
    crop_path: str | None = None
    status: CandidateStatus
    alignment_method: str
    model: str
    created_at: str  # ISO-8601, supplied by the caller

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


class CandidateProvenance(BaseModel):
    """Where a CandidateSet came from and how it was aligned (un-reproducible run metadata)."""

    source_page_id: str
    source_raster: str
    source_svg: str | None = None
    alignment_method: str
    alignment_residual_px: float | None = None
    model: str


class CandidateSet(BaseModel):
    """Contract emitted by ingest-segment and consumed by review.

    Serialized as a `candidates.json` manifest in a directory, with per-candidate
    `strokes/<id>.json` and `crop/<id>.png` sidecar files (see ingest-segment's
    candidates_out). Candidates are stored sorted lowest-confidence-first.
    """

    version: str = "1"
    provenance: CandidateProvenance
    candidates: list[Candidate] = Field(default_factory=list)

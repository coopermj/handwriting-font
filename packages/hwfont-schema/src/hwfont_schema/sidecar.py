from pydantic import BaseModel, Field

from hwfont_schema.geometry import BBox


class Region(BaseModel):
    """A ruled row on the capture template the writer fills in with a known prompt."""

    id: str
    expected_transcript: str
    baseline_y: float
    bbox: BBox
    expected_units: list[str] = Field(min_length=1)
    ligature_targets: list[str] = Field(default_factory=list)


class Page(BaseModel):
    """One page of the capture template, in source-image pixel coordinates."""

    id: str
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    dpi: int = Field(gt=0)
    regions: list[Region] = Field(default_factory=list)


class CaptureSidecar(BaseModel):
    """Contract X: emitted next to the capture PDF; tells ingest what each region holds and where."""

    version: str = "1"
    pages: list[Page] = Field(default_factory=list)

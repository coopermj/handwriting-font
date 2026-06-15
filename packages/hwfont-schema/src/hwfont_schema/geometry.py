from pydantic import BaseModel, Field


class BBox(BaseModel):
    """Axis-aligned bounding box. Units depend on context (page pixels or em-normalized)."""

    x: float
    y: float
    w: float = Field(gt=0)
    h: float = Field(gt=0)

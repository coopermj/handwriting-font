from pydantic import BaseModel, Field


class StrokePoint(BaseModel):
    """One sampled point along a pen stroke. `pressure` is None for raster-sourced ink."""

    x: float
    y: float
    pressure: float | None = None


class Contour(BaseModel):
    """An ordered sequence of points forming one continuous stroke."""

    points: list[StrokePoint] = Field(min_length=2)


class StrokeData(BaseModel):
    """All stroke geometry for a single captured glyph sample, em-normalized."""

    contours: list[Contour] = Field(min_length=1)

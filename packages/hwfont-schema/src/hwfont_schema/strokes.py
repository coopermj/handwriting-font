from pydantic import BaseModel, Field


class StrokePoint(BaseModel):
    """One sampled point along a pen stroke, em-v1 frame (hwfont_schema.emspace). `pressure` is None for raster-sourced ink; `width` is the local full ink width in em when the device reports it."""

    x: float
    y: float
    pressure: float | None = None
    width: float | None = None


class Contour(BaseModel):
    """An ordered sequence of points forming one continuous stroke."""

    points: list[StrokePoint] = Field(min_length=2)


class StrokeData(BaseModel):
    """All stroke geometry for a single captured glyph sample, em-normalized per hwfont_schema.emspace (em-v1): unsmoothed pen centerlines in writing order."""

    contours: list[Contour] = Field(min_length=1)

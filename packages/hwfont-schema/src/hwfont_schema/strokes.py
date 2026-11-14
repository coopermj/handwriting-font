from typing import Literal

from pydantic import BaseModel, Field


class StrokePoint(BaseModel):
    """One sampled point along a pen stroke.

    `pressure` is the device pen pressure (0..1) when the source carries it, else None.
    `width` is the measured ink width at this point, in the same units as x/y
    (page pixels for candidates, em-normalized for accepted samples), else None.
    """

    x: float
    y: float
    pressure: float | None = None
    width: float | None = None


class Contour(BaseModel):
    """An ordered sequence of points forming one continuous stroke."""

    points: list[StrokePoint] = Field(min_length=2)


class Outline(BaseModel):
    """The filled ink outline of one pen stroke: one or more closed rings plus the fill rule.

    This is the ground-truth ink shape (with its real, pressure-varying width); contours
    are the derived centerlines.
    """

    rings: list[list[StrokePoint]] = Field(min_length=1)
    fill_rule: Literal["nonzero", "evenodd"] = "nonzero"


class StrokeData(BaseModel):
    """All stroke geometry for a single captured glyph sample.

    `contours` are centerlines (required). `outlines` carry the original filled ink
    shapes when the source provides them (vector exports); None for raster-sourced ink.
    """

    contours: list[Contour] = Field(min_length=1)
    outlines: list[Outline] | None = None

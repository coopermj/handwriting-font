from __future__ import annotations

from font_gen.build import build_font
from font_gen.features import generate_features
from font_gen.outline import Outline, OutlineContour, outline_from_strokes
from font_gen.proof import render_proof
from font_gen.qa import Finding, QAReport
from font_gen.run import BuildResult, main
from font_gen.select import GlyphPlan, plan_glyphs
from font_gen.synth import default_targets, demo_spec, synth_store

__version__ = "0.1.0"

__all__ = [
    "BuildResult",
    "Finding",
    "GlyphPlan",
    "Outline",
    "OutlineContour",
    "QAReport",
    "build_font",
    "default_targets",
    "demo_spec",
    "generate_features",
    "main",
    "outline_from_strokes",
    "plan_glyphs",
    "render_proof",
    "synth_store",
    "__version__",
]

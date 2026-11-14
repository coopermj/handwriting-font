from ingest_segment.align import (
    Alignment,
    align_page,
    apply_affine,
    estimate_affine,
    residual,
    transform_stroke,
    warp_raster,
)
from ingest_segment.candidates_out import read_candidate_set, write_candidate_set
from ingest_segment.debug import render_overlay
from ingest_segment.raster import detect_fiducials, load_raster
from ingest_segment.remarkable_rm import load_rm_export
from ingest_segment.remarkable_svg import PenStroke, RemarkableExport, load_remarkable_export
from ingest_segment.run import ingest_page, main, prepare_page
from ingest_segment.segment import (
    ClaudeVisionClient,
    StrokeSummary,
    VisionBox,
    VisionResult,
    VisionUnit,
    derive_context,
    segment_region,
)

__version__ = "0.1.0"

__all__ = [
    "Alignment",
    "align_page",
    "apply_affine",
    "estimate_affine",
    "residual",
    "transform_stroke",
    "warp_raster",
    "read_candidate_set",
    "write_candidate_set",
    "render_overlay",
    "detect_fiducials",
    "load_raster",
    "ingest_page",
    "prepare_page",
    "main",
    "ClaudeVisionClient",
    "StrokeSummary",
    "VisionBox",
    "VisionResult",
    "VisionUnit",
    "derive_context",
    "segment_region",
    "PenStroke",
    "RemarkableExport",
    "load_remarkable_export",
    "load_rm_export",
    "__version__",
]

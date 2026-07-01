from ingest_segment.align import Alignment, align_page, apply_affine, estimate_affine, residual
from ingest_segment.candidates_out import read_candidate_set, write_candidate_set
from ingest_segment.raster import detect_fiducials, load_raster
from ingest_segment.run import ingest_page, main
from ingest_segment.segment import (
    ClaudeVisionClient,
    VisionBox,
    VisionResult,
    derive_context,
    segment_region,
)
from ingest_segment.remarkable_svg import RemarkableExport, load_remarkable_export

__version__ = "0.1.0"

__all__ = [
    "Alignment",
    "align_page",
    "apply_affine",
    "estimate_affine",
    "residual",
    "read_candidate_set",
    "write_candidate_set",
    "detect_fiducials",
    "load_raster",
    "ingest_page",
    "main",
    "ClaudeVisionClient",
    "VisionBox",
    "VisionResult",
    "derive_context",
    "segment_region",
    "RemarkableExport",
    "load_remarkable_export",
    "__version__",
]

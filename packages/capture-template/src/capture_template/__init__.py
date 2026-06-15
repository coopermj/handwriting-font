from capture_template.targets import default_targets, load_target_spec
from capture_template.planner import CoverageRow, PlanResult, PromptLine, count_occurrences, plan
from capture_template.layout import LayoutModel, LayoutPage, PageConfig, Row, build_layout, rows_per_page
from capture_template.sidecar_out import build_sidecar
from capture_template.generate import generate, UnmetCoverageError

__version__ = "0.1.0"

__all__ = [
    "generate",
    "UnmetCoverageError",
    "PageConfig",
    "Row",
    "LayoutPage",
    "LayoutModel",
    "build_layout",
    "rows_per_page",
    "plan",
    "count_occurrences",
    "PromptLine",
    "CoverageRow",
    "PlanResult",
    "build_sidecar",
    "default_targets",
    "load_target_spec",
    "__version__",
]

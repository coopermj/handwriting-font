from __future__ import annotations

from hwfont_schema import BBox, CaptureSidecar, Page, Region

from capture_template.layout import LayoutModel, fiducials


def build_sidecar(model: LayoutModel) -> CaptureSidecar:
    cfg = model.config
    pages: list[Page] = []
    for page in model.pages:
        regions: list[Region] = []
        for row_index, row in enumerate(page.rows):
            regions.append(
                Region(
                    id=f"p{page.index}-r{row_index}",
                    expected_transcript=row.expected_transcript,
                    baseline_y=row.baseline_y,
                    bbox=row.bbox,
                    expected_units=row.expected_units,
                    ligature_targets=row.ligature_targets,
                )
            )
        pages.append(
            Page(
                id=f"p{page.index}",
                width_px=cfg.width_px,
                height_px=cfg.height_px,
                dpi=cfg.dpi,
                source_bounds=BBox(x=0.0, y=0.0, w=float(cfg.width_px), h=float(cfg.height_px)),
                fiducials=fiducials(cfg),
                regions=regions,
            )
        )
    return CaptureSidecar(pages=pages)

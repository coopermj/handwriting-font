from __future__ import annotations

from pathlib import Path

from reportlab.pdfgen import canvas

from capture_template.layout import LayoutModel, fiducials


def px_to_pt(px: float, dpi: int) -> float:
    return px / dpi * 72.0


def render_pdf(model: LayoutModel, out_path: str | Path) -> None:
    cfg = model.config
    page_w_pt = px_to_pt(cfg.width_px, cfg.dpi)
    page_h_pt = px_to_pt(cfg.height_px, cfg.dpi)
    c = canvas.Canvas(str(out_path), pagesize=(page_w_pt, page_h_pt))

    for page in model.pages:
        for row in page.rows:
            x0 = px_to_pt(row.bbox.x, cfg.dpi)
            x1 = px_to_pt(row.bbox.x + row.bbox.w, cfg.dpi)

            # writing-line rule at baseline (convert top-left px-y to bottom-left pt-y)
            y_rule = page_h_pt - px_to_pt(row.baseline_y, cfg.dpi)
            c.setStrokeColorRGB(0.8, 0.8, 0.8)
            c.setLineWidth(1)
            c.line(x0, y_rule, x1, y_rule)

            # faint prompt text just above the writing area
            prompt_baseline_px = row.bbox.y - cfg.prompt_gap_px
            y_prompt = page_h_pt - px_to_pt(prompt_baseline_px, cfg.dpi)
            c.setFillColorRGB(0.6, 0.6, 0.6)
            c.setFont("Helvetica", px_to_pt(cfg.prompt_font_px, cfg.dpi))
            c.drawString(x0, y_prompt, row.prompt_text)

        # corner registration marks — solid dark dots at known page positions
        c.setFillColorRGB(0, 0, 0)
        r_pt = px_to_pt(cfg.fiducial_radius_px, cfg.dpi)
        for mark in fiducials(cfg):
            x_pt = px_to_pt(mark.x, cfg.dpi)
            y_pt = page_h_pt - px_to_pt(mark.y, cfg.dpi)
            c.circle(x_pt, y_pt, r_pt, stroke=0, fill=1)
        c.showPage()
    c.save()

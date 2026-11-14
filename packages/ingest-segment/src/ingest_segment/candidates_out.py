from __future__ import annotations

import shutil
from pathlib import Path

from hwfont_schema import (
    Candidate,
    CandidateProvenance,
    CandidateSet,
    Contour,
    Outline,
    StrokeData,
    StrokePoint,
)
from ingest_segment.remarkable_svg import PenStroke


def _strokes_to_data(strokes: list[PenStroke]) -> StrokeData | None:
    """Serialize pen strokes: every centerline becomes a Contour (with width/pressure per
    point); the original ink outlines ride along when the source had them."""
    contours: list[Contour] = []
    outlines: list[Outline] = []
    for stroke in strokes:
        pressures = stroke.pressures or [None] * len(stroke.centerlines)
        for line, widths, press in zip(stroke.centerlines, stroke.widths, pressures):
            if len(line) < 2:
                continue
            pts = [
                StrokePoint(x=x, y=y, width=w, pressure=(press[i] if press is not None else None))
                for i, ((x, y), w) in enumerate(zip(line, widths))
            ]
            contours.append(Contour(points=pts))
        if stroke.outline:
            outlines.append(
                Outline(
                    rings=[[StrokePoint(x=x, y=y) for x, y in ring] for ring in stroke.outline],
                    fill_rule=stroke.fill_rule,  # type: ignore[arg-type]
                )
            )
    if not contours:
        return None
    return StrokeData(contours=contours, outlines=outlines or None)


def write_candidate_set(
    out_dir: str | Path,
    provenance: CandidateProvenance,
    items: list[tuple[Candidate, list[PenStroke]]],
    crops: dict[str, bytes],
    force: bool = False,
) -> CandidateSet:
    """Write candidates.json + per-candidate stroke/crop files; return the manifest.

    `items` is (candidate, page-px strokes); `crops` maps candidate id -> PNG bytes.
    Candidates are stored sorted lowest-confidence-first for review.
    """
    out_dir = Path(out_dir)
    if out_dir.exists():
        if not force:
            raise FileExistsError(f"output dir already exists: {out_dir} (use force=True)")
        shutil.rmtree(out_dir)
    (out_dir / "strokes").mkdir(parents=True)
    (out_dir / "crop").mkdir(parents=True)

    stored: list[Candidate] = []
    for candidate, strokes in items:
        updates: dict[str, str] = {}
        data = _strokes_to_data(strokes)
        if data is not None:
            rel = f"strokes/{candidate.id}.json"
            (out_dir / rel).write_text(data.model_dump_json(), encoding="utf-8")
            updates["strokes_path"] = rel
        if candidate.id in crops:
            rel = f"crop/{candidate.id}.png"
            (out_dir / rel).write_bytes(crops[candidate.id])
            updates["crop_path"] = rel
        stored.append(candidate.model_copy(update=updates) if updates else candidate)

    stored.sort(key=lambda c: c.confidence)
    cs = CandidateSet(provenance=provenance, candidates=stored)
    (out_dir / "candidates.json").write_text(cs.model_dump_json(), encoding="utf-8")
    return cs


def read_candidate_set(out_dir: str | Path) -> CandidateSet:
    """Read a CandidateSet manifest from a directory."""
    manifest = Path(out_dir) / "candidates.json"
    return CandidateSet.model_validate_json(manifest.read_text(encoding="utf-8"))

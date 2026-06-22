from __future__ import annotations

import shutil
from pathlib import Path

from hwfont_schema import (
    Candidate,
    CandidateProvenance,
    CandidateSet,
    Contour,
    StrokeData,
    StrokePoint,
)


def _strokes_to_data(strokes: list[list[tuple[float, float]]]) -> StrokeData | None:
    contours = [
        Contour(points=[StrokePoint(x=x, y=y) for x, y in pts])
        for pts in strokes
        if len(pts) >= 2
    ]
    return StrokeData(contours=contours) if contours else None


def write_candidate_set(
    out_dir: str | Path,
    provenance: CandidateProvenance,
    items: list[tuple[Candidate, list[list[tuple[float, float]]]]],
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

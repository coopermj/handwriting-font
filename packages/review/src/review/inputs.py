from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from hwfont_schema import Candidate, CandidateSet, CaptureSidecar, Page, Region, StrokeData
from PIL import Image

from review.errors import ReviewError


@dataclass(frozen=True)
class PageInputs:
    page: Page
    candidate_set: CandidateSet
    candidate_dir: Path
    candidates: dict[str, Candidate]
    strokes: dict[str, StrokeData]
    crops: dict[str, bytes]
    raster: Image.Image | None

    def region(self, region_id: str) -> Region:
        for r in self.page.regions:
            if r.id == region_id:
                return r
        raise ReviewError(f"unknown region {region_id!r} on page {self.page.id!r}")

    def candidate_ids(self, region_id: str) -> tuple[str, ...]:
        return tuple(c.id for c in self.candidate_set.candidates if c.region_id == region_id)


@dataclass(frozen=True)
class ReviewInputs:
    sidecar: CaptureSidecar
    session_id: str
    pages: dict[str, PageInputs]

    def page_of(self, candidate_id: str) -> PageInputs:
        for pg in self.pages.values():
            if candidate_id in pg.candidates:
                return pg
        raise ReviewError(f"unknown candidate id {candidate_id!r}")

    def candidate(self, candidate_id: str) -> Candidate:
        return self.page_of(candidate_id).candidates[candidate_id]


def _read(path: Path, what: str, candidate_id: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ReviewError(f"candidate {candidate_id}: cannot read {what} {path}: {exc}") from exc


def _load_page(page: Page, candidate_dir: Path, raster_path: Path | None) -> PageInputs:
    manifest = candidate_dir / "candidates.json"
    try:
        cs = CandidateSet.model_validate_json(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReviewError(f"cannot read candidate manifest {manifest}: {exc}") from exc

    strokes: dict[str, StrokeData] = {}
    crops: dict[str, bytes] = {}
    for c in cs.candidates:
        if c.strokes_path:
            raw = _read(candidate_dir / c.strokes_path, "strokes file", c.id)
            try:
                strokes[c.id] = StrokeData.model_validate_json(raw)
            except ValueError as exc:
                raise ReviewError(f"candidate {c.id}: invalid strokes file: {exc}") from exc
        if c.crop_path:
            crops[c.id] = _read(candidate_dir / c.crop_path, "crop file", c.id)

    raster = None
    if raster_path is not None:
        try:
            with Image.open(raster_path) as im:
                raster = im.convert("L")
        except OSError as exc:
            raise ReviewError(f"cannot read raster {raster_path}: {exc}") from exc
        if raster.size != (page.width_px, page.height_px):
            raise ReviewError(
                f"raster size {raster.size} for page {page.id!r} does not match "
                f"sidecar page size {(page.width_px, page.height_px)}"
            )

    return PageInputs(
        page=page,
        candidate_set=cs,
        candidate_dir=candidate_dir,
        candidates={c.id: c for c in cs.candidates},
        strokes=strokes,
        crops=crops,
        raster=raster,
    )


def load_inputs(
    sidecar_path: Path,
    candidate_dirs: Sequence[Path],
    rasters: Mapping[str, Path] | None = None,
    session_id: str | None = None,
) -> ReviewInputs:
    sidecar_path = Path(sidecar_path)
    try:
        sidecar = CaptureSidecar.model_validate_json(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReviewError(f"cannot read sidecar {sidecar_path}: {exc}") from exc
    if session_id is None:
        session_id = sidecar_path.name.split(".")[0]

    by_id = {p.id: p for p in sidecar.pages}
    rasters = dict(rasters or {})
    for pid in rasters:
        if pid not in by_id:
            raise ReviewError(f"raster given for page {pid!r} which is not in the sidecar")

    pages: dict[str, PageInputs] = {}
    seen_ids: set[str] = set()
    for d in candidate_dirs:
        d = Path(d)
        manifest = d / "candidates.json"
        try:
            pid = CandidateSet.model_validate_json(manifest.read_text(encoding="utf-8")).provenance.source_page_id
        except (OSError, ValueError) as exc:
            raise ReviewError(f"cannot read candidate manifest {manifest}: {exc}") from exc
        if pid not in by_id:
            raise ReviewError(f"candidate set {d} refers to page {pid!r} which is not in the sidecar")
        if pid in pages:
            raise ReviewError(f"page {pid!r} loaded twice (candidate set {d})")
        pi = _load_page(by_id[pid], d, rasters.get(pid))
        dup = seen_ids & set(pi.candidates)
        if dup:
            raise ReviewError(f"candidate id {sorted(dup)[0]!r} appears twice across candidate sets")
        seen_ids |= set(pi.candidates)
        pages[pid] = pi

    return ReviewInputs(sidecar=sidecar, session_id=session_id, pages=pages)

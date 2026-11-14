from __future__ import annotations

import sqlite3
from pathlib import Path

from pydantic import BaseModel

from hwfont_schema.enums import Kind, PositionInWord, ReviewStatus
from hwfont_schema.sample import Sample, Target
from hwfont_schema.strokes import StrokeData

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sample (
    id               TEXT PRIMARY KEY,
    label            TEXT NOT NULL,
    kind             TEXT NOT NULL,
    position_in_word TEXT NOT NULL,
    review_status    TEXT NOT NULL,
    data             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sample_label ON sample(label);

CREATE TABLE IF NOT EXISTS target (
    label          TEXT NOT NULL,
    kind           TEXT NOT NULL,
    required_count INTEGER NOT NULL,
    PRIMARY KEY (label, kind)
);
"""


class CoverageRow(BaseModel):
    """One target's progress toward its required sample count (accepted samples only)."""

    label: str
    kind: str
    required: int
    accepted: int
    met: bool


class GlyphStore:
    """Contract Y store: a SQLite manifest plus on-disk stroke/raster sidecar files."""

    def __init__(self, root: Path, conn: sqlite3.Connection) -> None:
        self.root = root
        self._conn = conn

    @classmethod
    def create(cls, root: str | Path) -> "GlyphStore":
        root = Path(root)
        (root / "strokes").mkdir(parents=True, exist_ok=True)
        (root / "raster").mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(root / "store.db")
        conn.executescript(_SCHEMA)
        return cls(root, conn)

    @classmethod
    def open(cls, root: str | Path) -> "GlyphStore":
        root = Path(root)
        db_path = root / "store.db"
        if not db_path.exists():
            raise FileNotFoundError(f"No glyph store at {db_path}")
        conn = sqlite3.connect(db_path)
        return cls(root, conn)

    def close(self) -> None:
        self._conn.close()

    def add_sample(
        self,
        sample: Sample,
        strokes: StrokeData | None = None,
        raster: bytes | None = None,
    ) -> Sample:
        strokes_rel = f"strokes/{sample.id}.json" if strokes is not None else None
        raster_rel = f"raster/{sample.id}.png" if raster is not None else None

        updates = {}
        if strokes_rel is not None:
            updates["strokes_path"] = strokes_rel
        if raster_rel is not None:
            updates["raster_path"] = raster_rel
        if updates:
            sample = sample.model_copy(update=updates)

        written: list[Path] = []
        try:
            self._conn.execute(
                "INSERT INTO sample (id, label, kind, position_in_word, review_status, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    sample.id,
                    sample.label,
                    sample.kind.value,
                    sample.context.position_in_word.value,
                    sample.review_status.value,
                    sample.model_dump_json(),
                ),
            )
            if strokes is not None:
                path = self.root / strokes_rel
                path.write_text(strokes.model_dump_json(), encoding="utf-8")
                written.append(path)
            if raster is not None:
                path = self.root / raster_rel
                path.write_bytes(raster)
                written.append(path)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            for path in written:
                path.unlink(missing_ok=True)
            raise
        return sample

    def samples_for(
        self, label: str, position: PositionInWord | None = None
    ) -> list[Sample]:
        if position is None:
            rows = self._conn.execute(
                "SELECT data FROM sample WHERE label = ? ORDER BY id", (label,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT data FROM sample WHERE label = ? AND position_in_word = ? ORDER BY id",
                (label, position.value),
            ).fetchall()
        return [Sample.model_validate_json(row[0]) for row in rows]

    def all_samples(self, status: ReviewStatus | None = None) -> list[Sample]:
        """Every sample in the store, ordered by label then id; optionally one review status."""
        if status is None:
            rows = self._conn.execute("SELECT data FROM sample ORDER BY label, id").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT data FROM sample WHERE review_status = ? ORDER BY label, id",
                (status.value,),
            ).fetchall()
        return [Sample.model_validate_json(row[0]) for row in rows]

    def get_sample(self, sample_id: str) -> Sample | None:
        """The sample with this id, or None when absent."""
        row = self._conn.execute(
            "SELECT data FROM sample WHERE id = ?", (sample_id,)
        ).fetchone()
        return Sample.model_validate_json(row[0]) if row is not None else None

    def remove_sample(self, sample_id: str) -> bool:
        """Delete a sample's row and sidecar files; False (no-op) when the id is absent."""
        sample = self.get_sample(sample_id)
        if sample is None:
            return False
        try:
            self._conn.execute("DELETE FROM sample WHERE id = ?", (sample_id,))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        for rel in (sample.strokes_path, sample.raster_path):
            if rel is not None:
                (self.root / rel).unlink(missing_ok=True)
        return True

    def load_strokes(self, sample: Sample) -> StrokeData | None:
        """Read a sample's stroke sidecar, or None when the sample has no stroke data."""
        if sample.strokes_path is None:
            return None
        path = self.root / sample.strokes_path
        return StrokeData.model_validate_json(path.read_text(encoding="utf-8"))

    def load_raster(self, sample: Sample) -> bytes | None:
        """Read a sample's raster sidecar (PNG bytes), or None when the sample has no raster."""
        if sample.raster_path is None:
            return None
        return (self.root / sample.raster_path).read_bytes()

    def targets(self) -> list[Target]:
        """The target catalog, ordered by label."""
        rows = self._conn.execute(
            "SELECT label, kind, required_count FROM target ORDER BY label"
        ).fetchall()
        return [
            Target(label=label, kind=Kind(kind), required_count=required)
            for label, kind, required in rows
        ]

    def add_target(self, target: Target) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO target (label, kind, required_count) VALUES (?, ?, ?)",
            (target.label, target.kind.value, target.required_count),
        )
        self._conn.commit()

    def coverage(self) -> list[CoverageRow]:
        rows = self._conn.execute(
            "SELECT label, kind, required_count FROM target ORDER BY label"
        ).fetchall()
        result: list[CoverageRow] = []
        for label, kind, required in rows:
            (accepted,) = self._conn.execute(
                "SELECT COUNT(*) FROM sample WHERE label = ? AND kind = ? AND review_status = ?",
                (label, kind, ReviewStatus.accepted.value),
            ).fetchone()
            result.append(
                CoverageRow(
                    label=label,
                    kind=kind,
                    required=required,
                    accepted=accepted,
                    met=accepted >= required,
                )
            )
        return result

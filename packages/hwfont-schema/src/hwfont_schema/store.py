from __future__ import annotations

import sqlite3
from pathlib import Path

from pydantic import BaseModel

from hwfont_schema.enums import PositionInWord, ReviewStatus
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

        # Insert the manifest row first; a failure here (e.g. duplicate id) must
        # not leave orphaned sidecar files on disk.
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
            (self.root / strokes_rel).write_text(strokes.model_dump_json(), encoding="utf-8")
        if raster is not None:
            (self.root / raster_rel).write_bytes(raster)

        self._conn.commit()
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
                "SELECT COUNT(*) FROM sample WHERE label = ? AND review_status = ?",
                (label, ReviewStatus.accepted.value),
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

from __future__ import annotations

import sqlite3
from pathlib import Path

from pydantic import BaseModel

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
        conn.commit()
        return cls(root, conn)

    @classmethod
    def open(cls, root: str | Path) -> "GlyphStore":
        root = Path(root)
        conn = sqlite3.connect(root / "store.db")
        return cls(root, conn)

    def close(self) -> None:
        self._conn.close()

    def add_sample(
        self,
        sample: Sample,
        strokes: StrokeData | None = None,
        raster: bytes | None = None,
    ) -> Sample:
        if strokes is not None:
            rel = f"strokes/{sample.id}.json"
            (self.root / rel).write_text(strokes.model_dump_json(), encoding="utf-8")
            sample = sample.model_copy(update={"strokes_path": rel})
        if raster is not None:
            rel = f"raster/{sample.id}.png"
            (self.root / rel).write_bytes(raster)
            sample = sample.model_copy(update={"raster_path": rel})

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
        self._conn.commit()
        return sample

    def samples_for(self, label: str) -> list[Sample]:
        rows = self._conn.execute(
            "SELECT data FROM sample WHERE label = ? ORDER BY id", (label,)
        ).fetchall()
        return [Sample.model_validate_json(row[0]) for row in rows]

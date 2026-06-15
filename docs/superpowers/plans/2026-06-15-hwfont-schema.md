# hwfont-schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `hwfont-schema`, the shared contract package defining Contract X (capture sidecar) and Contract Y (glyph store) so the capture side and font side can be built and tested independently.

**Architecture:** A monorepo package (`packages/hwfont-schema/`) exposing pydantic models for both contracts, a JSON serialization path for the sidecar, and a `GlyphStore` class that wraps a SQLite manifest plus on-disk stroke/raster sidecar files. Every other module will depend on this package and on nothing else of each other.

**Tech Stack:** Python 3.12, pydantic v2 (validation + JSON round-trip), stdlib `sqlite3`, pytest, hatchling build backend.

---

## File Structure

```
packages/hwfont-schema/
  pyproject.toml                         # package metadata, deps, pytest config
  src/hwfont_schema/
    __init__.py                          # public exports
    geometry.py                          # BBox
    strokes.py                           # StrokePoint, Contour, StrokeData
    enums.py                             # Kind, PositionInWord, ReviewStatus, Quality
    sidecar.py                           # Contract X: Region, Page, CaptureSidecar
    sample.py                            # Contract Y models: Context, Metrics, Sample, Target
    store.py                             # GlyphStore (SQLite manifest + sidecar files), CoverageRow
  tests/
    test_geometry.py
    test_strokes.py
    test_sidecar.py
    test_sample.py
    test_store.py
    test_roundtrip.py
```

**Responsibilities:** model files hold only data definitions + validation; `store.py` is the only file that touches the filesystem or SQLite; tests mirror source files one-to-one.

**Conventions used throughout:**
- Timestamps are passed in by callers as ISO-8601 strings — the schema never calls `datetime.now()` (keeps models pure and tests deterministic).
- Sidecar/sample paths stored in the manifest are **relative to the store directory**.
- The SQLite `sample`/`target` rows keep indexed columns for querying plus a `data` JSON column holding the full pydantic dump, so rows round-trip losslessly.

---

### Task 1: Package scaffold

**Files:**
- Create: `packages/hwfont-schema/pyproject.toml`
- Create: `packages/hwfont-schema/src/hwfont_schema/__init__.py`
- Create: `packages/hwfont-schema/tests/test_smoke.py`

- [ ] **Step 1: Write the failing test**

`packages/hwfont-schema/tests/test_smoke.py`:
```python
def test_package_imports_and_has_version():
    import hwfont_schema

    assert hwfont_schema.__version__ == "0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_smoke.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hwfont_schema'`

- [ ] **Step 3: Write minimal implementation**

`packages/hwfont-schema/pyproject.toml`:
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "hwfont-schema"
version = "0.1.0"
description = "Shared data contracts for the handwriting-font pipeline."
requires-python = ">=3.12"
dependencies = ["pydantic>=2.6"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.hatch.build.targets.wheel]
packages = ["src/hwfont_schema"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`packages/hwfont-schema/src/hwfont_schema/__init__.py`:
```python
__version__ = "0.1.0"
```

- [ ] **Step 4: Install the package and run the test**

Run:
```bash
cd packages/hwfont-schema && pip install -e ".[dev]" && python -m pytest tests/test_smoke.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/hwfont-schema
git commit -m "feat(schema): scaffold hwfont-schema package"
```

---

### Task 2: Geometry primitive (BBox)

**Files:**
- Create: `packages/hwfont-schema/src/hwfont_schema/geometry.py`
- Test: `packages/hwfont-schema/tests/test_geometry.py`

- [ ] **Step 1: Write the failing test**

`tests/test_geometry.py`:
```python
import pytest
from pydantic import ValidationError

from hwfont_schema.geometry import BBox


def test_bbox_accepts_valid_values():
    box = BBox(x=1.0, y=2.0, w=3.0, h=4.0)
    assert (box.x, box.y, box.w, box.h) == (1.0, 2.0, 3.0, 4.0)


def test_bbox_rejects_nonpositive_width():
    with pytest.raises(ValidationError):
        BBox(x=0, y=0, w=0, h=5)


def test_bbox_rejects_nonpositive_height():
    with pytest.raises(ValidationError):
        BBox(x=0, y=0, w=5, h=-1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_geometry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hwfont_schema.geometry'`

- [ ] **Step 3: Write minimal implementation**

`src/hwfont_schema/geometry.py`:
```python
from pydantic import BaseModel, Field


class BBox(BaseModel):
    """Axis-aligned bounding box. Units depend on context (page pixels or em-normalized)."""

    x: float
    y: float
    w: float = Field(gt=0)
    h: float = Field(gt=0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_geometry.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/hwfont-schema/src/hwfont_schema/geometry.py packages/hwfont-schema/tests/test_geometry.py
git commit -m "feat(schema): add BBox geometry primitive"
```

---

### Task 3: Stroke data models

**Files:**
- Create: `packages/hwfont-schema/src/hwfont_schema/strokes.py`
- Test: `packages/hwfont-schema/tests/test_strokes.py`

- [ ] **Step 1: Write the failing test**

`tests/test_strokes.py`:
```python
import pytest
from pydantic import ValidationError

from hwfont_schema.strokes import Contour, StrokeData, StrokePoint


def test_stroke_point_pressure_optional():
    p = StrokePoint(x=1.0, y=2.0)
    assert p.pressure is None


def test_contour_requires_at_least_two_points():
    with pytest.raises(ValidationError):
        Contour(points=[StrokePoint(x=0, y=0)])


def test_stroke_data_requires_at_least_one_contour():
    with pytest.raises(ValidationError):
        StrokeData(contours=[])


def test_stroke_data_round_trips_through_json():
    data = StrokeData(
        contours=[
            Contour(points=[StrokePoint(x=0, y=0, pressure=0.5), StrokePoint(x=1, y=1)])
        ]
    )
    restored = StrokeData.model_validate_json(data.model_dump_json())
    assert restored == data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_strokes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hwfont_schema.strokes'`

- [ ] **Step 3: Write minimal implementation**

`src/hwfont_schema/strokes.py`:
```python
from pydantic import BaseModel, Field


class StrokePoint(BaseModel):
    """One sampled point along a pen stroke. `pressure` is None for raster-sourced ink."""

    x: float
    y: float
    pressure: float | None = None


class Contour(BaseModel):
    """An ordered sequence of points forming one continuous stroke."""

    points: list[StrokePoint] = Field(min_length=2)


class StrokeData(BaseModel):
    """All stroke geometry for a single captured glyph sample, em-normalized."""

    contours: list[Contour] = Field(min_length=1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_strokes.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/hwfont-schema/src/hwfont_schema/strokes.py packages/hwfont-schema/tests/test_strokes.py
git commit -m "feat(schema): add stroke geometry models"
```

---

### Task 4: Contract X — capture sidecar

**Files:**
- Create: `packages/hwfont-schema/src/hwfont_schema/sidecar.py`
- Test: `packages/hwfont-schema/tests/test_sidecar.py`

- [ ] **Step 1: Write the failing test**

`tests/test_sidecar.py`:
```python
import pytest
from pydantic import ValidationError

from hwfont_schema.geometry import BBox
from hwfont_schema.sidecar import CaptureSidecar, Page, Region


def _region() -> Region:
    return Region(
        id="r1",
        expected_transcript="the quick brown fox",
        baseline_y=120.0,
        bbox=BBox(x=0, y=80, w=600, h=60),
        expected_units=["t", "h", "e", "q", "u", "i", "c", "k"],
        ligature_targets=["ck"],
    )


def test_region_requires_at_least_one_expected_unit():
    with pytest.raises(ValidationError):
        Region(
            id="r1",
            expected_transcript="x",
            baseline_y=0.0,
            bbox=BBox(x=0, y=0, w=1, h=1),
            expected_units=[],
        )


def test_page_rejects_nonpositive_dimensions():
    with pytest.raises(ValidationError):
        Page(id="p1", width_px=0, height_px=100, dpi=300)


def test_sidecar_defaults_version_and_round_trips():
    sidecar = CaptureSidecar(
        pages=[Page(id="p1", width_px=1404, height_px=1872, dpi=226, regions=[_region()])]
    )
    assert sidecar.version == "1"
    restored = CaptureSidecar.model_validate_json(sidecar.model_dump_json())
    assert restored == sidecar
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_sidecar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hwfont_schema.sidecar'`

- [ ] **Step 3: Write minimal implementation**

`src/hwfont_schema/sidecar.py`:
```python
from pydantic import BaseModel, Field

from hwfont_schema.geometry import BBox


class Region(BaseModel):
    """A ruled row on the capture template the writer fills in with a known prompt."""

    id: str
    expected_transcript: str
    baseline_y: float
    bbox: BBox
    expected_units: list[str] = Field(min_length=1)
    ligature_targets: list[str] = Field(default_factory=list)


class Page(BaseModel):
    """One page of the capture template, in source-image pixel coordinates."""

    id: str
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    dpi: int = Field(gt=0)
    regions: list[Region] = Field(default_factory=list)


class CaptureSidecar(BaseModel):
    """Contract X: emitted next to the capture PDF; tells ingest what each region holds and where."""

    version: str = "1"
    pages: list[Page] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_sidecar.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/hwfont-schema/src/hwfont_schema/sidecar.py packages/hwfont-schema/tests/test_sidecar.py
git commit -m "feat(schema): add Contract X capture sidecar models"
```

---

### Task 5: Contract Y — sample/target models and enums

**Files:**
- Create: `packages/hwfont-schema/src/hwfont_schema/enums.py`
- Create: `packages/hwfont-schema/src/hwfont_schema/sample.py`
- Test: `packages/hwfont-schema/tests/test_sample.py`

- [ ] **Step 1: Write the failing test**

`tests/test_sample.py`:
```python
import pytest
from pydantic import ValidationError

from hwfont_schema.enums import Kind, PositionInWord, Quality, ReviewStatus
from hwfont_schema.geometry import BBox
from hwfont_schema.sample import Context, Metrics, Sample, Target


def _sample(**overrides) -> Sample:
    base = dict(
        id="s1",
        label="a",
        kind=Kind.single,
        strokes_path="strokes/s1.json",
        raster_path="raster/s1.png",
        context=Context(
            source_word="cat",
            left_neighbor="c",
            right_neighbor="t",
            position_in_word=PositionInWord.medial,
        ),
        metrics=Metrics(baseline=0.0, x_height=0.5, advance=0.6, bbox=BBox(x=0, y=0, w=0.5, h=0.5)),
        quality=Quality.good,
        review_status=ReviewStatus.accepted,
        capture_session_id="sess-1",
        created_at="2026-06-15T00:00:00Z",
    )
    base.update(overrides)
    return Sample(**base)


def test_sample_round_trips_through_json():
    s = _sample()
    restored = Sample.model_validate_json(s.model_dump_json())
    assert restored == s


def test_metrics_rejects_nonpositive_advance():
    with pytest.raises(ValidationError):
        Metrics(baseline=0.0, x_height=0.5, advance=0.0, bbox=BBox(x=0, y=0, w=1, h=1))


def test_target_requires_positive_required_count():
    with pytest.raises(ValidationError):
        Target(label="eft", kind=Kind.ligature, required_count=0)


def test_enums_serialize_to_their_string_values():
    s = _sample(review_status=ReviewStatus.needs_review)
    assert '"review_status":"needs_review"' in s.model_dump_json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_sample.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hwfont_schema.enums'`

- [ ] **Step 3: Write minimal implementation**

`src/hwfont_schema/enums.py`:
```python
from enum import Enum


class Kind(str, Enum):
    single = "single"
    ligature = "ligature"


class PositionInWord(str, Enum):
    initial = "initial"
    medial = "medial"
    final = "final"
    isolated = "isolated"


class ReviewStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    needs_review = "needs_review"


class Quality(str, Enum):
    good = "good"
    marginal = "marginal"
    bad = "bad"
```

`src/hwfont_schema/sample.py`:
```python
from pydantic import BaseModel, Field

from hwfont_schema.enums import Kind, PositionInWord, Quality, ReviewStatus
from hwfont_schema.geometry import BBox


class Context(BaseModel):
    """Where a sample came from — drives contextual alternates in font-gen."""

    source_word: str
    left_neighbor: str | None = None
    right_neighbor: str | None = None
    position_in_word: PositionInWord


class Metrics(BaseModel):
    """Em-normalized typographic metrics for one sample."""

    baseline: float
    x_height: float
    advance: float = Field(gt=0)
    bbox: BBox


class Sample(BaseModel):
    """Contract Y: one reviewed, accepted glyph/ligature sample. Ink lives in sidecar files."""

    id: str
    label: str
    kind: Kind
    strokes_path: str | None = None
    raster_path: str | None = None
    context: Context
    metrics: Metrics
    quality: Quality
    review_status: ReviewStatus
    capture_session_id: str
    created_at: str  # ISO-8601, supplied by the caller


class Target(BaseModel):
    """A glyph or ligature we want, with how many samples are required for good coverage."""

    label: str
    kind: Kind
    required_count: int = Field(ge=1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_sample.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/hwfont-schema/src/hwfont_schema/enums.py packages/hwfont-schema/src/hwfont_schema/sample.py packages/hwfont-schema/tests/test_sample.py
git commit -m "feat(schema): add Contract Y sample/target models and enums"
```

---

### Task 6: GlyphStore — create/open and write samples

**Files:**
- Create: `packages/hwfont-schema/src/hwfont_schema/store.py`
- Test: `packages/hwfont-schema/tests/test_store.py`

- [ ] **Step 1: Write the failing test**

`tests/test_store.py`:
```python
from pathlib import Path

from hwfont_schema.enums import Kind, PositionInWord, Quality, ReviewStatus
from hwfont_schema.geometry import BBox
from hwfont_schema.sample import Context, Metrics, Sample, Target
from hwfont_schema.store import GlyphStore
from hwfont_schema.strokes import Contour, StrokeData, StrokePoint


def _sample(sample_id: str, label: str, position=PositionInWord.medial) -> Sample:
    return Sample(
        id=sample_id,
        label=label,
        kind=Kind.single,
        context=Context(source_word="cat", position_in_word=position),
        metrics=Metrics(baseline=0.0, x_height=0.5, advance=0.6, bbox=BBox(x=0, y=0, w=0.5, h=0.5)),
        quality=Quality.good,
        review_status=ReviewStatus.accepted,
        capture_session_id="sess-1",
        created_at="2026-06-15T00:00:00Z",
    )


def _strokes() -> StrokeData:
    return StrokeData(contours=[Contour(points=[StrokePoint(x=0, y=0), StrokePoint(x=1, y=1)])])


def test_create_initializes_directory_layout(tmp_path: Path):
    store = GlyphStore.create(tmp_path / "store")
    assert (tmp_path / "store" / "store.db").exists()
    assert (tmp_path / "store" / "strokes").is_dir()
    assert (tmp_path / "store" / "raster").is_dir()
    store.close()


def test_add_sample_writes_stroke_sidecar_and_sets_relative_path(tmp_path: Path):
    store = GlyphStore.create(tmp_path / "store")
    stored = store.add_sample(_sample("s1", "a"), strokes=_strokes(), raster=b"\x89PNG_fake")
    store.close()

    assert stored.strokes_path == "strokes/s1.json"
    assert stored.raster_path == "raster/s1.png"
    assert (tmp_path / "store" / "strokes" / "s1.json").exists()
    assert (tmp_path / "store" / "raster" / "s1.png").read_bytes() == b"\x89PNG_fake"


def test_reopen_and_read_sample_back(tmp_path: Path):
    store = GlyphStore.create(tmp_path / "store")
    store.add_sample(_sample("s1", "a"), strokes=_strokes(), raster=None)
    store.close()

    reopened = GlyphStore.open(tmp_path / "store")
    got = reopened.samples_for("a")
    reopened.close()

    assert len(got) == 1
    assert got[0].id == "s1"
    assert got[0].label == "a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'hwfont_schema.store'`

- [ ] **Step 3: Write minimal implementation**

`src/hwfont_schema/store.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_store.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/hwfont-schema/src/hwfont_schema/store.py packages/hwfont-schema/tests/test_store.py
git commit -m "feat(schema): add GlyphStore create/open and sample writes"
```

---

### Task 7: GlyphStore — position queries and coverage report

**Files:**
- Modify: `packages/hwfont-schema/src/hwfont_schema/store.py`
- Test: `packages/hwfont-schema/tests/test_store.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_store.py`:
```python
from hwfont_schema.store import CoverageRow


def test_samples_for_filters_by_position(tmp_path: Path):
    store = GlyphStore.create(tmp_path / "store")
    store.add_sample(_sample("s1", "a", PositionInWord.initial))
    store.add_sample(_sample("s2", "a", PositionInWord.final))
    only_final = store.samples_for("a", position=PositionInWord.final)
    store.close()

    assert [s.id for s in only_final] == ["s2"]


def test_coverage_counts_accepted_samples_against_targets(tmp_path: Path):
    store = GlyphStore.create(tmp_path / "store")
    store.add_target(Target(label="a", kind=Kind.single, required_count=2))
    store.add_target(Target(label="eft", kind=Kind.ligature, required_count=3))
    store.add_sample(_sample("s1", "a"))
    store.add_sample(_sample("s2", "a"))
    coverage = {row.label: row for row in store.coverage()}
    store.close()

    assert coverage["a"] == CoverageRow(label="a", kind="single", required=2, accepted=2, met=True)
    assert coverage["eft"] == CoverageRow(
        label="eft", kind="ligature", required=3, accepted=0, met=False
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_store.py -v`
Expected: FAIL — `TypeError: samples_for() got an unexpected keyword argument 'position'` and `AttributeError: 'GlyphStore' object has no attribute 'add_target'`

- [ ] **Step 3: Write minimal implementation**

In `src/hwfont_schema/store.py`, add the import at the top:
```python
from hwfont_schema.enums import PositionInWord
```

Replace the `samples_for` method with this version and add the two new methods to the `GlyphStore` class:
```python
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
                "SELECT COUNT(*) FROM sample WHERE label = ? AND review_status = 'accepted'",
                (label,),
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_store.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/hwfont-schema/src/hwfont_schema/store.py packages/hwfont-schema/tests/test_store.py
git commit -m "feat(schema): add position queries and coverage report to GlyphStore"
```

---

### Task 8: Public exports and full round-trip integration test

**Files:**
- Modify: `packages/hwfont-schema/src/hwfont_schema/__init__.py`
- Test: `packages/hwfont-schema/tests/test_roundtrip.py`

- [ ] **Step 1: Write the failing test**

`tests/test_roundtrip.py`:
```python
from pathlib import Path

import hwfont_schema as hs


def test_top_level_exports_present():
    for name in [
        "BBox",
        "StrokeData",
        "Contour",
        "StrokePoint",
        "CaptureSidecar",
        "Page",
        "Region",
        "Sample",
        "Target",
        "Context",
        "Metrics",
        "Kind",
        "PositionInWord",
        "ReviewStatus",
        "Quality",
        "GlyphStore",
        "CoverageRow",
    ]:
        assert hasattr(hs, name), f"missing export: {name}"


def test_end_to_end_sidecar_and_store(tmp_path: Path):
    sidecar = hs.CaptureSidecar(
        pages=[
            hs.Page(
                id="p1",
                width_px=1404,
                height_px=1872,
                dpi=226,
                regions=[
                    hs.Region(
                        id="r1",
                        expected_transcript="cat",
                        baseline_y=100.0,
                        bbox=hs.BBox(x=0, y=60, w=300, h=60),
                        expected_units=["c", "a", "t"],
                    )
                ],
            )
        ]
    )
    assert hs.CaptureSidecar.model_validate_json(sidecar.model_dump_json()) == sidecar

    store = hs.GlyphStore.create(tmp_path / "store")
    store.add_target(hs.Target(label="a", kind=hs.Kind.single, required_count=1))
    store.add_sample(
        hs.Sample(
            id="s1",
            label="a",
            kind=hs.Kind.single,
            context=hs.Context(source_word="cat", position_in_word=hs.PositionInWord.medial),
            metrics=hs.Metrics(
                baseline=0.0, x_height=0.5, advance=0.6, bbox=hs.BBox(x=0, y=0, w=0.5, h=0.5)
            ),
            quality=hs.Quality.good,
            review_status=hs.ReviewStatus.accepted,
            capture_session_id="sess-1",
            created_at="2026-06-15T00:00:00Z",
        ),
        strokes=hs.StrokeData(
            contours=[hs.Contour(points=[hs.StrokePoint(x=0, y=0), hs.StrokePoint(x=1, y=1)])]
        ),
    )
    coverage = store.coverage()
    store.close()

    assert coverage[0].met is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_roundtrip.py -v`
Expected: FAIL with `AssertionError: missing export: BBox`

- [ ] **Step 3: Write minimal implementation**

Replace `src/hwfont_schema/__init__.py`:
```python
from hwfont_schema.enums import Kind, PositionInWord, Quality, ReviewStatus
from hwfont_schema.geometry import BBox
from hwfont_schema.sample import Context, Metrics, Sample, Target
from hwfont_schema.sidecar import CaptureSidecar, Page, Region
from hwfont_schema.store import CoverageRow, GlyphStore
from hwfont_schema.strokes import Contour, StrokeData, StrokePoint

__version__ = "0.1.0"

__all__ = [
    "BBox",
    "StrokeData",
    "Contour",
    "StrokePoint",
    "CaptureSidecar",
    "Page",
    "Region",
    "Sample",
    "Target",
    "Context",
    "Metrics",
    "Kind",
    "PositionInWord",
    "ReviewStatus",
    "Quality",
    "GlyphStore",
    "CoverageRow",
    "__version__",
]
```

- [ ] **Step 4: Run the full test suite**

Run: `cd packages/hwfont-schema && python -m pytest -v`
Expected: PASS (all tests across every file green)

- [ ] **Step 5: Commit**

```bash
git add packages/hwfont-schema/src/hwfont_schema/__init__.py packages/hwfont-schema/tests/test_roundtrip.py
git commit -m "feat(schema): expose public API and add end-to-end round-trip test"
```

---

## Notes for the Implementer

- **Why pydantic v2:** the module's whole job is validating data at contract boundaries. Pydantic gives validation, lossless JSON round-trip, and constraint enforcement (`Field(gt=0)`, `min_length`) with almost no hand-written code.
- **Why the `data` JSON column:** indexed columns (`label`, `position_in_word`, `review_status`) make the queries `font-gen` and `review` need fast, while the full JSON dump in `data` guarantees a row reconstructs to the exact pydantic model — no column-by-column mapping to drift out of sync.
- **Timestamps in, not generated:** `created_at` is a caller-supplied ISO string so the schema stays a pure data layer and tests are deterministic.
- **`model_copy(update=...)`:** used in `add_sample` to attach the sidecar-relative paths without mutating the caller's object.
```

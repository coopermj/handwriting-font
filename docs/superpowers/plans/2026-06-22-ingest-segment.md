# ingest-segment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `ingest-segment` module — turn a written-on reMarkable capture page (raster PNG + optional SVG ink) plus its Contract X sidecar into a confidence-bearing `CandidateSet` of proposed glyph/ligature samples, using Claude Opus 4.8 vision for the automated first pass.

**Architecture:** Two precursor changes land first — a `Candidate`/`CandidateSet` contract and `Page.fiducials` in `hwfont-schema`, and 4 corner fiducial marks printed + recorded by `capture-template`. Then a new `packages/ingest-segment/` package implements a linear pipeline: parse SVG ink → load raster → detect fiducials and compute an affine alignment → per Contract X region crop the raster, call Claude vision for labeled boxes, map boxes onto aligned strokes, derive transcript context → emit a `CandidateSet` (JSON manifest + per-candidate stroke/crop files). The vision call is mocked in unit tests; one env-gated integration test hits the real API.

**Tech Stack:** Python 3.12, pydantic v2, hatchling, pytest. New runtime deps for `ingest-segment`: `anthropic` (Claude Opus 4.8 vision + structured outputs), `pillow` (raster crop + blob detection), `numpy` (affine least-squares), `svgpathtools` (SVG ink parsing). `cairosvg` is an optional dep used only to rasterize an SVG-only input.

**Spec:** [docs/superpowers/specs/2026-06-18-ingest-segment-design.md](../specs/2026-06-18-ingest-segment-design.md)

---

## Conventions (follow exactly — match the existing two packages)

- **Layout:** each package is `packages/<name>/` with `pyproject.toml`, `src/<module>/`, `tests/`. Build backend `hatchling`; `[tool.pytest.ini_options] testpaths = ["tests"]`.
- **Pydantic models:** `from pydantic import BaseModel, Field`, one class per concept, a one-line docstring on each. Constraints via `Field(gt=0)`, `Field(ge=1)`, `Field(min_length=1)`, `Field(default_factory=list)`. Match `packages/hwfont-schema/src/hwfont_schema/sample.py`.
- **Enums:** `class X(str, Enum)` with lowercase members, in `enums.py`. Match `packages/hwfont-schema/src/hwfont_schema/enums.py`.
- **`__init__.py`:** re-export new public names and add them to `__all__`, matching `packages/hwfont-schema/src/hwfont_schema/__init__.py`.
- **`created_at`:** ISO-8601 string, **caller-supplied** (never generated inside a model) — same as `Sample.created_at`.
- **Imports across packages:** `from hwfont_schema import BBox, Kind, ...` (top-level re-exports).
- **Tests:** `pytest`, plain `def test_*` functions, `tmp_path` fixture for filesystem work. Match `packages/hwfont-schema/tests/test_roundtrip.py`.
- **Run a single package's tests:** `cd packages/<name> && python -m pytest -q` (each package has its own `testpaths`). Install editable first: `pip install -e packages/hwfont-schema -e packages/capture-template -e packages/ingest-segment`.

---

## File Structure

**`hwfont-schema` (modify):**
- `src/hwfont_schema/enums.py` — add `CandidateStatus`.
- `src/hwfont_schema/candidate.py` — **new** — `Candidate`, `CandidateSet`, `CandidateProvenance` models.
- `src/hwfont_schema/sidecar.py` — add `Fiducial` model and `Page.fiducials`.
- `src/hwfont_schema/__init__.py` — re-export the new names.

**`capture-template` (modify):**
- `src/capture_template/layout.py` — add fiducial-geometry config + `fiducials()` helper.
- `src/capture_template/sidecar_out.py` — populate `Page.fiducials`.
- `src/capture_template/pdf.py` — render the 4 marks per page.

**`ingest-segment` (new package):**
- `src/ingest_segment/svg_strokes.py` — parse SVG → per-stroke geometry; separate ink from template.
- `src/ingest_segment/raster.py` — load page raster; detect fiducials.
- `src/ingest_segment/align.py` — fiducials → affine; geometric-scale fallback; transform strokes & points; provenance.
- `src/ingest_segment/segment.py` — per region: crop, vision call (Claude Opus 4.8), parse, map boxes→strokes, derive context, flag.
- `src/ingest_segment/candidates_out.py` — write/read `CandidateSet` to/from disk.
- `src/ingest_segment/run.py` — orchestrate + `argparse` CLI.
- `tests/` — one test file per module plus `test_end_to_end.py`.

---

## Phase 1 — `hwfont-schema`: the `Candidate` contract + fiducials

### Task 1: `CandidateStatus` enum

**Files:**
- Modify: `packages/hwfont-schema/src/hwfont_schema/enums.py`
- Test: `packages/hwfont-schema/tests/test_candidate.py`

- [ ] **Step 1: Write the failing test**

Create `packages/hwfont-schema/tests/test_candidate.py`:

```python
from hwfont_schema import CandidateStatus


def test_candidate_status_members():
    assert CandidateStatus.pending.value == "pending"
    assert CandidateStatus.needs_review.value == "needs_review"
    assert {s.value for s in CandidateStatus} == {"pending", "needs_review"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_candidate.py -q`
Expected: FAIL with `ImportError: cannot import name 'CandidateStatus'`

- [ ] **Step 3: Add the enum**

Append to `packages/hwfont-schema/src/hwfont_schema/enums.py`:

```python
class CandidateStatus(str, Enum):
    pending = "pending"
    needs_review = "needs_review"
```

- [ ] **Step 4: Add the re-export**

In `packages/hwfont-schema/src/hwfont_schema/__init__.py`, change the enums import line and `__all__`:

```python
from hwfont_schema.enums import (
    CandidateStatus,
    Kind,
    PositionInWord,
    Quality,
    ReviewStatus,
)
```

Add `"CandidateStatus",` to the `__all__` list (next to `"Kind",`).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_candidate.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/hwfont-schema/src/hwfont_schema/enums.py packages/hwfont-schema/src/hwfont_schema/__init__.py packages/hwfont-schema/tests/test_candidate.py
git commit -m "feat(schema): add CandidateStatus enum"
```

---

### Task 2: `Candidate` model

**Files:**
- Create: `packages/hwfont-schema/src/hwfont_schema/candidate.py`
- Modify: `packages/hwfont-schema/src/hwfont_schema/__init__.py`
- Test: `packages/hwfont-schema/tests/test_candidate.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/hwfont-schema/tests/test_candidate.py`:

```python
import pytest
from pydantic import ValidationError

from hwfont_schema import BBox, Candidate, Context, Kind, PositionInWord


def _candidate(**overrides):
    base = dict(
        id="c1",
        page_id="p0",
        region_id="p0-r0",
        label="a",
        kind=Kind.single,
        confidence=0.8,
        bbox=BBox(x=10, y=20, w=30, h=40),
        context=Context(source_word="cat", position_in_word=PositionInWord.medial),
        status=CandidateStatus.pending,
        alignment_method="fiducial",
        model="claude-opus-4-8",
        created_at="2026-06-22T00:00:00Z",
    )
    base.update(overrides)
    return Candidate(**base)


def test_candidate_roundtrip_and_optional_paths():
    c = _candidate()
    assert c.strokes_path is None and c.crop_path is None
    assert Candidate.model_validate_json(c.model_dump_json()) == c


def test_candidate_confidence_bounds():
    with pytest.raises(ValidationError):
        _candidate(confidence=1.5)
    with pytest.raises(ValidationError):
        _candidate(confidence=-0.1)
```

The test module already imports `CandidateStatus` at the top (from Task 1). Add `CandidateStatus` to the top-of-file import so the helper resolves:

```python
from hwfont_schema import CandidateStatus
```

(keep the existing first-line import from Task 1 — this is the same symbol; ensure it is imported once at module top.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_candidate.py -q`
Expected: FAIL with `ImportError: cannot import name 'Candidate'`

- [ ] **Step 3: Create the model**

Create `packages/hwfont-schema/src/hwfont_schema/candidate.py`:

```python
from pydantic import BaseModel, Field

from hwfont_schema.enums import CandidateStatus, Kind
from hwfont_schema.geometry import BBox
from hwfont_schema.sample import Context


class Candidate(BaseModel):
    """A proposed-but-unconfirmed glyph/ligature sample from the vision first pass.

    Geometry (`bbox`) is in Contract X page-pixel space, un-normalized. On accept
    in `review`, it is normalized to em-space and written as a `Sample`.
    """

    id: str
    page_id: str
    region_id: str
    label: str
    kind: Kind
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: BBox
    context: Context
    strokes_path: str | None = None
    crop_path: str | None = None
    status: CandidateStatus
    alignment_method: str
    model: str
    created_at: str  # ISO-8601, supplied by the caller
```

- [ ] **Step 4: Add the re-export**

In `packages/hwfont-schema/src/hwfont_schema/__init__.py`, add after the `geometry` import:

```python
from hwfont_schema.candidate import Candidate
```

Add `"Candidate",` to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_candidate.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/hwfont-schema/src/hwfont_schema/candidate.py packages/hwfont-schema/src/hwfont_schema/__init__.py packages/hwfont-schema/tests/test_candidate.py
git commit -m "feat(schema): add Candidate contract"
```

---

### Task 3: `CandidateProvenance` + `CandidateSet` manifest models

**Files:**
- Modify: `packages/hwfont-schema/src/hwfont_schema/candidate.py`
- Modify: `packages/hwfont-schema/src/hwfont_schema/__init__.py`
- Test: `packages/hwfont-schema/tests/test_candidate.py`

These are the in-memory manifest models. Disk IO (writing `candidates.json` + per-candidate files) lives in `ingest-segment/candidates_out.py` (Task 13).

- [ ] **Step 1: Write the failing test**

Append to `packages/hwfont-schema/tests/test_candidate.py`:

```python
from hwfont_schema import CandidateProvenance, CandidateSet


def test_candidate_set_roundtrip_and_provenance():
    cs = CandidateSet(
        provenance=CandidateProvenance(
            source_page_id="p0",
            source_raster="page0.png",
            source_svg="page0.svg",
            alignment_method="fiducial",
            alignment_residual_px=1.4,
            model="claude-opus-4-8",
        ),
        candidates=[_candidate(id="c1", confidence=0.9), _candidate(id="c2", confidence=0.2)],
    )
    assert CandidateSet.model_validate_json(cs.model_dump_json()) == cs
    # provenance fields that are unknown for a raster-only run are optional
    prov = CandidateProvenance(
        source_page_id="p0",
        source_raster="page0.png",
        alignment_method="geometric_scale",
        model="claude-opus-4-8",
    )
    assert prov.source_svg is None and prov.alignment_residual_px is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_candidate.py -q`
Expected: FAIL with `ImportError: cannot import name 'CandidateSet'`

- [ ] **Step 3: Add the models**

Append to `packages/hwfont-schema/src/hwfont_schema/candidate.py`:

```python
class CandidateProvenance(BaseModel):
    """Where a CandidateSet came from and how it was aligned (un-reproducible run metadata)."""

    source_page_id: str
    source_raster: str
    source_svg: str | None = None
    alignment_method: str
    alignment_residual_px: float | None = None
    model: str


class CandidateSet(BaseModel):
    """Contract emitted by ingest-segment and consumed by review.

    Serialized as a `candidates.json` manifest in a directory, with per-candidate
    `strokes/<id>.json` and `crop/<id>.png` sidecar files (see ingest-segment's
    candidates_out). Candidates are stored sorted lowest-confidence-first.
    """

    version: str = "1"
    provenance: CandidateProvenance
    candidates: list[Candidate] = Field(default_factory=list)
```

- [ ] **Step 4: Add the re-exports**

In `packages/hwfont-schema/src/hwfont_schema/__init__.py`, change the candidate import:

```python
from hwfont_schema.candidate import Candidate, CandidateProvenance, CandidateSet
```

Add `"CandidateProvenance",` and `"CandidateSet",` to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_candidate.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/hwfont-schema/src/hwfont_schema/candidate.py packages/hwfont-schema/src/hwfont_schema/__init__.py packages/hwfont-schema/tests/test_candidate.py
git commit -m "feat(schema): add CandidateSet + provenance manifest models"
```

---

### Task 4: `Fiducial` model + `Page.fiducials`

**Files:**
- Modify: `packages/hwfont-schema/src/hwfont_schema/sidecar.py`
- Modify: `packages/hwfont-schema/src/hwfont_schema/__init__.py`
- Test: `packages/hwfont-schema/tests/test_sidecar.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/hwfont-schema/tests/test_sidecar.py`:

```python
from hwfont_schema import Fiducial, Page


def test_page_fiducials_default_empty_and_roundtrip():
    page = Page(id="p0", width_px=1404, height_px=1872, dpi=226)
    assert page.fiducials == []

    page2 = Page(
        id="p0",
        width_px=1404,
        height_px=1872,
        dpi=226,
        fiducials=[
            Fiducial(id="tl", x=40.0, y=40.0),
            Fiducial(id="tr", x=1364.0, y=40.0),
            Fiducial(id="bl", x=40.0, y=1832.0),
            Fiducial(id="br", x=1364.0, y=1832.0),
        ],
    )
    assert Page.model_validate_json(page2.model_dump_json()) == page2
    assert {f.id for f in page2.fiducials} == {"tl", "tr", "bl", "br"}
```

If `test_sidecar.py` has no imports yet, add `from hwfont_schema import Fiducial, Page` (and keep any existing imports).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_sidecar.py -q`
Expected: FAIL with `ImportError: cannot import name 'Fiducial'`

- [ ] **Step 3: Add the model + field**

In `packages/hwfont-schema/src/hwfont_schema/sidecar.py`, add a `Fiducial` class above `Region`:

```python
class Fiducial(BaseModel):
    """A printed corner registration mark, at a known center in source-image pixels."""

    id: str
    x: float
    y: float
```

Add the field to `Page` (after `source_bounds`):

```python
    fiducials: list[Fiducial] = Field(default_factory=list)
```

- [ ] **Step 4: Add the re-export**

In `packages/hwfont-schema/src/hwfont_schema/__init__.py`, change the sidecar import:

```python
from hwfont_schema.sidecar import CaptureSidecar, Fiducial, Page, Region
```

Add `"Fiducial",` to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd packages/hwfont-schema && python -m pytest tests/test_sidecar.py -q`
Expected: PASS

- [ ] **Step 6: Run the full schema suite + commit**

Run: `cd packages/hwfont-schema && python -m pytest -q`
Expected: PASS (all existing tests still green)

```bash
git add packages/hwfont-schema/src/hwfont_schema/sidecar.py packages/hwfont-schema/src/hwfont_schema/__init__.py packages/hwfont-schema/tests/test_sidecar.py
git commit -m "feat(schema): add Fiducial corner marks to Contract X Page"
```

---

## Phase 2 — `capture-template`: print + record fiducials

### Task 5: compute fiducial positions in layout

**Files:**
- Modify: `packages/capture-template/src/capture_template/layout.py`
- Test: `packages/capture-template/tests/test_layout.py`

`PageConfig` gets two new fields; a `fiducials(config)` helper returns the 4 corner centers, inset from the page edges. These are pure geometry, identical for every page.

- [ ] **Step 1: Write the failing test**

Append to `packages/capture-template/tests/test_layout.py`:

```python
from capture_template.layout import PageConfig, fiducials


def _cfg(**overrides):
    base = dict(
        width_px=1404,
        height_px=1872,
        dpi=226,
        margin_px=80,
        prompt_font_px=28,
        prompt_gap_px=12,
        line_height_px=70,
        row_pitch_px=150,
    )
    base.update(overrides)
    return PageConfig(**base)


def test_fiducials_are_four_inset_corners():
    cfg = _cfg(fiducial_inset_px=40)
    marks = fiducials(cfg)
    assert [m.id for m in marks] == ["tl", "tr", "bl", "br"]
    by_id = {m.id: (m.x, m.y) for m in marks}
    assert by_id["tl"] == (40.0, 40.0)
    assert by_id["tr"] == (1404.0 - 40.0, 40.0)
    assert by_id["bl"] == (40.0, 1872.0 - 40.0)
    assert by_id["br"] == (1404.0 - 40.0, 1872.0 - 40.0)


def test_fiducial_radius_default():
    assert _cfg().fiducial_radius_px == 12
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python -m pytest tests/test_layout.py -q`
Expected: FAIL with `ImportError: cannot import name 'fiducials'`

- [ ] **Step 3: Add config fields + helper**

In `packages/capture-template/src/capture_template/layout.py`, add two fields to the `PageConfig` dataclass (after `max_line_chars`):

```python
    fiducial_inset_px: int = 40
    fiducial_radius_px: int = 12
```

Add this import near the top (the file already imports from `hwfont_schema`):

```python
from hwfont_schema import Fiducial
```

Add the helper function (place it next to `rows_per_page`):

```python
def fiducials(config: PageConfig) -> list[Fiducial]:
    """The 4 corner registration marks for a page, inset from each edge."""
    inset = config.fiducial_inset_px
    right = config.width_px - inset
    bottom = config.height_px - inset
    return [
        Fiducial(id="tl", x=float(inset), y=float(inset)),
        Fiducial(id="tr", x=float(right), y=float(inset)),
        Fiducial(id="bl", x=float(inset), y=float(bottom)),
        Fiducial(id="br", x=float(right), y=float(bottom)),
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python -m pytest tests/test_layout.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/capture-template/src/capture_template/layout.py packages/capture-template/tests/test_layout.py
git commit -m "feat(capture): compute 4 corner fiducial positions"
```

---

### Task 6: record fiducials in the sidecar

**Files:**
- Modify: `packages/capture-template/src/capture_template/sidecar_out.py`
- Test: `packages/capture-template/tests/test_sidecar_out.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/capture-template/tests/test_sidecar_out.py` (mirror the existing test's model construction style — build a tiny `LayoutModel` with a `PageConfig` and one `LayoutPage`):

```python
from capture_template.layout import LayoutModel, LayoutPage, PageConfig
from capture_template.sidecar_out import build_sidecar


def _cfg():
    return PageConfig(
        width_px=1404, height_px=1872, dpi=226, margin_px=80,
        prompt_font_px=28, prompt_gap_px=12, line_height_px=70,
        row_pitch_px=150, fiducial_inset_px=40,
    )


def test_sidecar_pages_carry_fiducials():
    model = LayoutModel(config=_cfg(), pages=[LayoutPage(index=0)])
    sidecar = build_sidecar(model)
    page = sidecar.pages[0]
    assert [f.id for f in page.fiducials] == ["tl", "tr", "bl", "br"]
    assert (page.fiducials[0].x, page.fiducials[0].y) == (40.0, 40.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python -m pytest tests/test_sidecar_out.py -q`
Expected: FAIL — `page.fiducials` is `[]`

- [ ] **Step 3: Populate fiducials**

In `packages/capture-template/src/capture_template/sidecar_out.py`, change the import line to add `fiducials`:

```python
from capture_template.layout import LayoutModel, fiducials
```

In `build_sidecar`, pass `fiducials=fiducials(cfg)` to the `Page(...)` constructor (alongside `source_bounds=...`):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python -m pytest tests/test_sidecar_out.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/capture-template/src/capture_template/sidecar_out.py packages/capture-template/tests/test_sidecar_out.py
git commit -m "feat(capture): record fiducials in Contract X sidecar"
```

---

### Task 7: render fiducial marks in the PDF

**Files:**
- Modify: `packages/capture-template/src/capture_template/pdf.py`
- Test: `packages/capture-template/tests/test_pdf.py`

The marks are filled dark circles at the 4 corner centers, drawn once per page. Use reportlab's `circle` with fill. Coordinates convert page-px → pt and flip the y-axis, exactly like the existing rule/prompt drawing.

- [ ] **Step 1: Write the failing test**

`test_pdf.py` uses `pypdf` (a dev dep). reportlab does not expose a "list shapes" API, so assert on the rendered PDF's vector content stream — filled-circle marks emit Bézier (`c`) + fill (`f`) operators that an empty page lacks. Append:

```python
from pypdf import PdfReader

from capture_template.layout import LayoutModel, LayoutPage, PageConfig
from capture_template.pdf import render_pdf


def _cfg():
    return PageConfig(
        width_px=1404, height_px=1872, dpi=226, margin_px=80,
        prompt_font_px=28, prompt_gap_px=12, line_height_px=70,
        row_pitch_px=150, fiducial_inset_px=40, fiducial_radius_px=12,
    )


def test_pdf_draws_fiducial_marks(tmp_path):
    model = LayoutModel(config=_cfg(), pages=[LayoutPage(index=0)])
    out = tmp_path / "capture.pdf"
    render_pdf(model, out)

    reader = PdfReader(str(out))
    content = reader.pages[0].get_contents().get_data().decode("latin-1")
    # filled fiducial circles emit Bézier curve ops ('c') and a fill ('f')
    assert " c\n" in content or " c " in content
    assert "\nf\n" in content or " f\n" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/capture-template && python -m pytest tests/test_pdf.py::test_pdf_draws_fiducial_marks -q`
Expected: FAIL — no fill/curve operators (the current PDF only draws line rules + text)

- [ ] **Step 3: Draw the marks**

In `packages/capture-template/src/capture_template/pdf.py`, change the import to pull in the helper:

```python
from capture_template.layout import LayoutModel, fiducials
```

In `render_pdf`, inside the `for page in model.pages:` loop, after the `for row in page.rows:` block and **before** `c.showPage()`, add:

```python
        # corner registration marks — solid dark dots at known page positions
        c.setFillColorRGB(0, 0, 0)
        r_pt = px_to_pt(cfg.fiducial_radius_px, cfg.dpi)
        for mark in fiducials(cfg):
            x_pt = px_to_pt(mark.x, cfg.dpi)
            y_pt = page_h_pt - px_to_pt(mark.y, cfg.dpi)
            c.circle(x_pt, y_pt, r_pt, stroke=0, fill=1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/capture-template && python -m pytest tests/test_pdf.py::test_pdf_draws_fiducial_marks -q`
Expected: PASS

- [ ] **Step 5: Run the full capture suite + commit**

Run: `cd packages/capture-template && python -m pytest -q`
Expected: PASS (all existing tests still green)

```bash
git add packages/capture-template/src/capture_template/pdf.py packages/capture-template/tests/test_pdf.py
git commit -m "feat(capture): render corner fiducial marks in capture PDF"
```

---

## Phase 3 — `ingest-segment` package scaffold

### Task 8: package skeleton

**Files:**
- Create: `packages/ingest-segment/pyproject.toml`
- Create: `packages/ingest-segment/src/ingest_segment/__init__.py`
- Create: `packages/ingest-segment/tests/test_smoke.py`

- [ ] **Step 1: Write the failing test**

Create `packages/ingest-segment/tests/test_smoke.py`:

```python
import ingest_segment


def test_version_present():
    assert ingest_segment.__version__ == "0.1.0"
```

- [ ] **Step 2: Create `pyproject.toml`**

Create `packages/ingest-segment/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "ingest-segment"
version = "0.1.0"
description = "Segment a written-on capture page into candidate glyph samples (Claude vision)."
requires-python = ">=3.12"
dependencies = [
    "hwfont-schema",
    "anthropic>=0.40",
    "pillow>=10.0",
    "numpy>=1.26",
    "svgpathtools>=1.6",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]
svg-raster = ["cairosvg>=2.7"]

[project.scripts]
ingest-segment = "ingest_segment.run:main"

[tool.hatch.build.targets.wheel]
packages = ["src/ingest_segment"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Create `__init__.py`**

Create `packages/ingest-segment/src/ingest_segment/__init__.py`:

```python
__version__ = "0.1.0"

__all__ = ["__version__"]
```

- [ ] **Step 4: Install editable + run the test**

Run: `pip install -e packages/ingest-segment && cd packages/ingest-segment && python -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/pyproject.toml packages/ingest-segment/src/ingest_segment/__init__.py packages/ingest-segment/tests/test_smoke.py
git commit -m "feat(ingest): scaffold ingest-segment package"
```

---

## Phase 4 — `svg_strokes`: parse ink, separate from template

### Task 9: parse an SVG into per-stroke geometry

**Files:**
- Create: `packages/ingest-segment/src/ingest_segment/svg_strokes.py`
- Test: `packages/ingest-segment/tests/test_svg_strokes.py`

A parsed stroke is an ordered list of `(x, y)` points in the SVG's own coordinate space, carrying its source `stroke` color so the next step can separate ink from template. We reuse `hwfont_schema.StrokeData`/`Contour`/`StrokePoint` for geometry but need the color, so define a small internal `RawStroke` dataclass here.

- [ ] **Step 1: Write the failing test**

Create `packages/ingest-segment/tests/test_svg_strokes.py`:

```python
from ingest_segment.svg_strokes import RawStroke, parse_svg_strokes

# two polylines: one near-black (ink), one light gray (template rule)
SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
  <path d="M 10,10 L 20,20 L 30,15" stroke="#111111" fill="none"/>
  <path d="M 0,50 L 100,50" stroke="#cccccc" fill="none"/>
</svg>"""


def test_parse_returns_strokes_with_points_and_color(tmp_path):
    svg_path = tmp_path / "page.svg"
    svg_path.write_text(SVG, encoding="utf-8")

    strokes = parse_svg_strokes(svg_path)

    assert len(strokes) == 2
    assert all(isinstance(s, RawStroke) for s in strokes)
    dark = [s for s in strokes if s.is_dark(threshold=0.5)]
    assert len(dark) == 1
    # points are sampled in order along the path; endpoints land near the path ends
    pts = dark[0].points
    assert len(pts) >= 2
    assert abs(pts[0][0] - 10) < 2 and abs(pts[0][1] - 10) < 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python -m pytest tests/test_svg_strokes.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest_segment.svg_strokes'`

- [ ] **Step 3: Implement the parser**

Create `packages/ingest-segment/src/ingest_segment/svg_strokes.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from svgpathtools import svg2paths2

# how many points to sample along each path segment
_SAMPLES_PER_SEG = 8


def _parse_color_luminance(stroke: str | None) -> float:
    """Relative luminance 0 (black) .. 1 (white) for an SVG stroke color; white if unknown."""
    if not stroke:
        return 1.0
    s = stroke.strip().lower()
    if s in ("none", "transparent"):
        return 1.0
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) == 6:
            r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
            return 0.2126 * r + 0.7152 * g + 0.0722 * b
    if s == "black":
        return 0.0
    if s == "white":
        return 1.0
    return 0.5  # unknown named color — neither clearly ink nor clearly template


@dataclass
class RawStroke:
    """One parsed SVG path: ordered points (SVG coords) plus its stroke luminance."""

    points: list[tuple[float, float]]
    luminance: float

    def is_dark(self, threshold: float = 0.5) -> bool:
        return self.luminance < threshold


def parse_svg_strokes(svg_path: str | Path) -> list[RawStroke]:
    """Parse an SVG export into per-path strokes (ordered points + stroke luminance)."""
    paths, attributes, _ = svg2paths2(str(svg_path))
    strokes: list[RawStroke] = []
    for path, attrs in zip(paths, attributes):
        pts: list[tuple[float, float]] = []
        for seg in path:
            for i in range(_SAMPLES_PER_SEG + 1):
                t = i / _SAMPLES_PER_SEG
                p = seg.point(t)
                pt = (float(p.real), float(p.imag))
                if not pts or pt != pts[-1]:
                    pts.append(pt)
        if len(pts) >= 2:
            strokes.append(RawStroke(points=pts, luminance=_parse_color_luminance(attrs.get("stroke"))))
    return strokes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python -m pytest tests/test_svg_strokes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/svg_strokes.py packages/ingest-segment/tests/test_svg_strokes.py
git commit -m "feat(ingest): parse SVG paths into per-stroke geometry"
```

---

### Task 10: separate ink from printed template

**Files:**
- Modify: `packages/ingest-segment/src/ingest_segment/svg_strokes.py`
- Test: `packages/ingest-segment/tests/test_svg_strokes.py`

The export contains both the printed template (light gray, ~0.6–0.8 luminance) and the writer's ink (near-black). `separate_ink` keeps only dark strokes. This is the most assumption-dependent function (per the spec's Known Risk) — it lives behind this clean interface so it can be swapped against a real export.

- [ ] **Step 1: Write the failing test**

Append to `packages/ingest-segment/tests/test_svg_strokes.py`:

```python
from ingest_segment.svg_strokes import separate_ink


def test_separate_ink_keeps_only_dark_strokes():
    strokes = [
        RawStroke(points=[(0, 0), (1, 1)], luminance=0.05),  # ink
        RawStroke(points=[(0, 0), (1, 0)], luminance=0.7),   # template gray
    ]
    ink = separate_ink(strokes)
    assert len(ink) == 1
    assert ink[0].luminance == 0.05
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python -m pytest tests/test_svg_strokes.py::test_separate_ink_keeps_only_dark_strokes -q`
Expected: FAIL with `ImportError: cannot import name 'separate_ink'`

- [ ] **Step 3: Implement**

Append to `packages/ingest-segment/src/ingest_segment/svg_strokes.py`:

```python
# template rules/prompts render light gray (~0.6-0.8); ink is near-black.
_INK_LUMINANCE_THRESHOLD = 0.5


def separate_ink(strokes: list[RawStroke], threshold: float = _INK_LUMINANCE_THRESHOLD) -> list[RawStroke]:
    """Keep only the writer's ink strokes, dropping the printed template by luminance."""
    return [s for s in strokes if s.is_dark(threshold)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python -m pytest tests/test_svg_strokes.py -q`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/svg_strokes.py packages/ingest-segment/tests/test_svg_strokes.py
git commit -m "feat(ingest): separate writer ink from printed template by luminance"
```

---

## Phase 5 — `raster`: load page image + detect fiducials

### Task 11: load a raster and detect fiducial centers

**Files:**
- Create: `packages/ingest-segment/src/ingest_segment/raster.py`
- Test: `packages/ingest-segment/tests/test_raster.py`

`load_raster` returns a Pillow grayscale image. `detect_fiducials` takes the loaded image plus the *expected* fiducial positions (from the sidecar) and finds the dark-blob centroid within a search window around each expected position, returning `{id: (x, y)}` for every mark it locates with enough dark pixels.

- [ ] **Step 1: Write the failing test**

Create `packages/ingest-segment/tests/test_raster.py`:

```python
from PIL import Image, ImageDraw

from hwfont_schema import Fiducial
from ingest_segment.raster import detect_fiducials, load_raster


def _page_with_dots(tmp_path, marks, radius=10):
    img = Image.new("L", (200, 200), color=255)
    draw = ImageDraw.Draw(img)
    for m in marks:
        draw.ellipse([m.x - radius, m.y - radius, m.x + radius, m.y + radius], fill=0)
    path = tmp_path / "page.png"
    img.save(path)
    return path


def test_load_raster_returns_grayscale(tmp_path):
    path = _page_with_dots(tmp_path, [Fiducial(id="tl", x=20, y=20)])
    img = load_raster(path)
    assert img.mode == "L"
    assert img.size == (200, 200)


def test_detect_fiducials_recovers_known_centers(tmp_path):
    expected = [
        Fiducial(id="tl", x=20, y=20),
        Fiducial(id="tr", x=180, y=20),
        Fiducial(id="bl", x=20, y=180),
        Fiducial(id="br", x=180, y=180),
    ]
    path = _page_with_dots(tmp_path, expected)
    img = load_raster(path)

    found = detect_fiducials(img, expected, search_radius=30)
    assert set(found) == {"tl", "tr", "bl", "br"}
    for m in expected:
        fx, fy = found[m.id]
        assert abs(fx - m.x) <= 1.5 and abs(fy - m.y) <= 1.5


def test_detect_fiducials_skips_missing_marks(tmp_path):
    expected = [Fiducial(id="tl", x=20, y=20), Fiducial(id="tr", x=180, y=20)]
    # only draw tl
    path = _page_with_dots(tmp_path, [expected[0]])
    img = load_raster(path)
    found = detect_fiducials(img, expected, search_radius=30)
    assert set(found) == {"tl"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python -m pytest tests/test_raster.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest_segment.raster'`

- [ ] **Step 3: Implement**

Create `packages/ingest-segment/src/ingest_segment/raster.py`:

```python
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from hwfont_schema import Fiducial

# a fiducial search window must contain at least this many dark pixels to count
_MIN_DARK_PIXELS = 8
# 0..255 grayscale; below this is "dark ink/mark"
_DARK_LEVEL = 96


def load_raster(path: str | Path) -> Image.Image:
    """Load a page raster as an 8-bit grayscale Pillow image."""
    return Image.open(path).convert("L")


def detect_fiducials(
    image: Image.Image,
    expected: list[Fiducial],
    search_radius: int = 60,
    dark_level: int = _DARK_LEVEL,
) -> dict[str, tuple[float, float]]:
    """Find each expected mark's dark-pixel centroid within a window around its known position.

    Returns {fiducial_id: (x, y)} for every mark with enough dark pixels; marks not
    found (too few dark pixels) are omitted so the caller can fall back.
    """
    arr = np.asarray(image, dtype=np.uint8)
    h, w = arr.shape
    found: dict[str, tuple[float, float]] = {}
    for mark in expected:
        x0 = max(0, int(mark.x - search_radius))
        x1 = min(w, int(mark.x + search_radius))
        y0 = max(0, int(mark.y - search_radius))
        y1 = min(h, int(mark.y + search_radius))
        window = arr[y0:y1, x0:x1]
        ys, xs = np.where(window < dark_level)
        if xs.size < _MIN_DARK_PIXELS:
            continue
        found[mark.id] = (float(xs.mean()) + x0, float(ys.mean()) + y0)
    return found
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python -m pytest tests/test_raster.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/raster.py packages/ingest-segment/tests/test_raster.py
git commit -m "feat(ingest): load page raster and detect fiducial centroids"
```

---

## Phase 6 — `align`: fiducials → affine, with fallback + provenance

### Task 12: estimate an affine and transform points

**Files:**
- Create: `packages/ingest-segment/src/ingest_segment/align.py`
- Test: `packages/ingest-segment/tests/test_align.py`

`estimate_affine(measured, expected)` solves a least-squares 2×3 affine mapping measured → expected positions (matched by fiducial id). `apply_affine(matrix, points)` transforms a list of `(x, y)`. `residual(matrix, measured, expected)` returns the RMS reprojection error in pixels.

- [ ] **Step 1: Write the failing test**

Create `packages/ingest-segment/tests/test_align.py`:

```python
import math

from hwfont_schema import Fiducial
from ingest_segment.align import apply_affine, estimate_affine, residual


def test_recovers_known_scale_and_translation():
    expected = [
        Fiducial(id="tl", x=40, y=40),
        Fiducial(id="tr", x=1360, y=40),
        Fiducial(id="bl", x=40, y=1830),
        Fiducial(id="br", x=1360, y=1830),
    ]
    # measured = expected scaled 0.5 and shifted (+5, -3)
    measured = {f.id: (f.x * 0.5 + 5.0, f.y * 0.5 - 3.0) for f in expected}

    m = estimate_affine(measured, expected)
    # applying the affine to measured points should recover expected
    for f in expected:
        ex, ey = apply_affine(m, [measured[f.id]])[0]
        assert abs(ex - f.x) < 1e-6 and abs(ey - f.y) < 1e-6
    assert residual(m, measured, expected) < 1e-6


def test_residual_reports_misfit():
    expected = [
        Fiducial(id="tl", x=0, y=0),
        Fiducial(id="tr", x=100, y=0),
        Fiducial(id="bl", x=0, y=100),
        Fiducial(id="br", x=100, y=100),
    ]
    measured = {f.id: (f.x, f.y) for f in expected}
    measured["br"] = (110.0, 90.0)  # perturb one mark
    m = estimate_affine(measured, expected)
    assert residual(m, measured, expected) > 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python -m pytest tests/test_align.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest_segment.align'`

- [ ] **Step 3: Implement the affine core**

Create `packages/ingest-segment/src/ingest_segment/align.py`:

```python
from __future__ import annotations

import numpy as np

from hwfont_schema import Fiducial

# 2x3 affine: [[a, b, tx], [c, d, ty]] mapping (x, y, 1) -> (x', y')
Affine = np.ndarray


def estimate_affine(
    measured: dict[str, tuple[float, float]], expected: list[Fiducial]
) -> Affine:
    """Least-squares 2x3 affine mapping measured fiducial positions onto expected ones.

    Only ids present in both `measured` and `expected` are used. Raises ValueError
    if fewer than 3 correspondences (an affine needs 3 non-collinear points).
    """
    pairs = [(measured[f.id], (f.x, f.y)) for f in expected if f.id in measured]
    if len(pairs) < 3:
        raise ValueError(f"need >=3 fiducial correspondences, got {len(pairs)}")
    src = np.array([[mx, my, 1.0] for (mx, my), _ in pairs])
    dst = np.array([[ex, ey] for _, (ex, ey) in pairs])
    # solve src @ P = dst  for P (3x2); affine rows are P transposed
    p, *_ = np.linalg.lstsq(src, dst, rcond=None)
    return p.T  # shape (2, 3)


def apply_affine(matrix: Affine, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Apply a 2x3 affine to a list of (x, y) points."""
    if not points:
        return []
    pts = np.array([[x, y, 1.0] for x, y in points])
    out = pts @ matrix.T  # (n, 2)
    return [(float(x), float(y)) for x, y in out]


def residual(
    matrix: Affine, measured: dict[str, tuple[float, float]], expected: list[Fiducial]
) -> float:
    """RMS reprojection error (pixels) of measured fiducials mapped onto expected."""
    ids = [f.id for f in expected if f.id in measured]
    src = [measured[i] for i in ids]
    proj = apply_affine(matrix, src)
    exp = {f.id: (f.x, f.y) for f in expected}
    errs = [
        (px - exp[i][0]) ** 2 + (py - exp[i][1]) ** 2
        for i, (px, py) in zip(ids, proj)
    ]
    return float(np.sqrt(np.mean(errs))) if errs else 0.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python -m pytest tests/test_align.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/align.py packages/ingest-segment/tests/test_align.py
git commit -m "feat(ingest): estimate affine from fiducials with reprojection residual"
```

---

### Task 13: alignment driver with geometric-scale fallback

**Files:**
- Modify: `packages/ingest-segment/src/ingest_segment/align.py`
- Test: `packages/ingest-segment/tests/test_align.py`

`align_page(measured, page, export_size)` chooses the alignment method and returns an `Alignment` (matrix + method string + residual + a `low_confidence` flag). Fiducials when ≥3 are found and residual is under threshold; otherwise geometric-scale from the export dimensions to the page dimensions; if even that can't be computed, raise.

- [ ] **Step 1: Write the failing test**

Append to `packages/ingest-segment/tests/test_align.py`:

```python
from hwfont_schema import Page
from ingest_segment.align import Alignment, align_page, apply_affine


def _page(fids):
    return Page(id="p0", width_px=200, height_px=200, dpi=226, fiducials=fids)


def test_align_page_uses_fiducials_when_available():
    fids = [
        Fiducial(id="tl", x=20, y=20),
        Fiducial(id="tr", x=180, y=20),
        Fiducial(id="bl", x=20, y=180),
        Fiducial(id="br", x=180, y=180),
    ]
    measured = {f.id: (f.x, f.y) for f in fids}  # identity export
    al = align_page(measured, _page(fids), export_size=(200, 200))
    assert isinstance(al, Alignment)
    assert al.method == "fiducial"
    assert al.low_confidence is False
    assert al.residual_px < 1e-6


def test_align_page_falls_back_to_geometric_scale():
    fids = [Fiducial(id="tl", x=20, y=20)]  # too few to estimate an affine
    # export is half-size; geometric scale should map export px -> page px (x2)
    al = align_page({}, _page(fids), export_size=(100, 100))
    assert al.method == "geometric_scale"
    sx, sy = apply_affine(al.matrix, [(50.0, 50.0)])[0]
    assert abs(sx - 100.0) < 1e-6 and abs(sy - 100.0) < 1e-6


def test_align_page_flags_low_confidence_on_high_residual():
    fids = [
        Fiducial(id="tl", x=0, y=0),
        Fiducial(id="tr", x=100, y=0),
        Fiducial(id="bl", x=0, y=100),
        Fiducial(id="br", x=100, y=100),
    ]
    measured = {f.id: (f.x, f.y) for f in fids}
    measured["br"] = (140.0, 60.0)  # large misfit
    al = align_page(measured, _page(fids), export_size=(100, 100), residual_threshold=2.0)
    assert al.method == "fiducial"
    assert al.low_confidence is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python -m pytest tests/test_align.py -q`
Expected: FAIL with `ImportError: cannot import name 'Alignment'`

- [ ] **Step 3: Implement the driver**

Append to `packages/ingest-segment/src/ingest_segment/align.py`:

```python
from dataclasses import dataclass

from hwfont_schema import Page

_DEFAULT_RESIDUAL_THRESHOLD = 6.0


@dataclass
class Alignment:
    """The chosen export->page-pixel transform plus how it was derived."""

    matrix: Affine
    method: str  # "fiducial" | "geometric_scale"
    residual_px: float | None
    low_confidence: bool


def _geometric_scale(export_size: tuple[int, int], page: Page) -> Affine:
    ew, eh = export_size
    if ew <= 0 or eh <= 0:
        raise ValueError(f"invalid export size {export_size}")
    sx = page.width_px / ew
    sy = page.height_px / eh
    return np.array([[sx, 0.0, 0.0], [0.0, sy, 0.0]])


def align_page(
    measured: dict[str, tuple[float, float]],
    page: Page,
    export_size: tuple[int, int],
    residual_threshold: float = _DEFAULT_RESIDUAL_THRESHOLD,
) -> Alignment:
    """Pick an export->page-pixel affine: fiducials if usable, else geometric scale."""
    usable = [f for f in page.fiducials if f.id in measured]
    if len(usable) >= 3:
        matrix = estimate_affine(measured, page.fiducials)
        res = residual(matrix, measured, page.fiducials)
        return Alignment(
            matrix=matrix,
            method="fiducial",
            residual_px=res,
            low_confidence=res > residual_threshold,
        )
    # fallback: scale export dimensions onto the sidecar's page dimensions
    matrix = _geometric_scale(export_size, page)
    return Alignment(matrix=matrix, method="geometric_scale", residual_px=None, low_confidence=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python -m pytest tests/test_align.py -q`
Expected: PASS (all align tests)

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/align.py packages/ingest-segment/tests/test_align.py
git commit -m "feat(ingest): alignment driver with geometric-scale fallback + confidence flag"
```

---

## Phase 7 — `segment`: crop, vision, map, derive context

### Task 14: transcript-derived context

**Files:**
- Create: `packages/ingest-segment/src/ingest_segment/segment.py`
- Test: `packages/ingest-segment/tests/test_segment_context.py`

`derive_context(transcript, label, unit_index)` builds a `hwfont_schema.Context` from the *known* prompt text. `unit_index` is the index into the region's `expected_units` (the non-space characters, in order — matching how `capture-template` builds `expected_units`). Neighbors are the adjacent non-space characters within the same word (`None` at a word boundary); `position_in_word` is initial/medial/final/isolated within that word.

- [ ] **Step 1: Write the failing test**

Create `packages/ingest-segment/tests/test_segment_context.py`:

```python
from hwfont_schema import PositionInWord
from ingest_segment.segment import derive_context

TRANSCRIPT = "the cat"  # units: t,h,e,c,a,t  -> indices 0..5


def test_context_medial_with_neighbors():
    ctx = derive_context(TRANSCRIPT, "a", unit_index=4)  # 'a' in "cat"
    assert ctx.source_word == "cat"
    assert ctx.position_in_word == PositionInWord.medial
    assert ctx.left_neighbor == "c"
    assert ctx.right_neighbor == "t"


def test_context_initial_and_final():
    initial = derive_context(TRANSCRIPT, "c", unit_index=3)  # 'c' starts "cat"
    assert initial.position_in_word == PositionInWord.initial
    assert initial.left_neighbor is None and initial.right_neighbor == "a"

    final = derive_context(TRANSCRIPT, "e", unit_index=2)  # 'e' ends "the"
    assert final.position_in_word == PositionInWord.final
    assert final.left_neighbor == "h" and final.right_neighbor is None


def test_context_isolated_single_char_word():
    ctx = derive_context("a cat", "a", unit_index=0)  # word "a"
    assert ctx.position_in_word == PositionInWord.isolated
    assert ctx.left_neighbor is None and ctx.right_neighbor is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python -m pytest tests/test_segment_context.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest_segment.segment'`

- [ ] **Step 3: Implement context derivation**

Create `packages/ingest-segment/src/ingest_segment/segment.py`:

```python
from __future__ import annotations

from hwfont_schema import Context, PositionInWord


def _unit_map(transcript: str) -> list[tuple[str, int, int, int]]:
    """For each non-space char (in order): (char, word_index, pos_in_word, word_len)."""
    out: list[tuple[str, int, int, int]] = []
    word_index = -1
    pos_in_word = 0
    prev_space = True
    # precompute word lengths (non-space run lengths)
    words = transcript.split()
    lengths = [len(w) for w in words]
    for ch in transcript:
        if ch.isspace():
            prev_space = True
            continue
        if prev_space:
            word_index += 1
            pos_in_word = 0
            prev_space = False
        out.append((ch, word_index, pos_in_word, lengths[word_index]))
        pos_in_word += 1
    return out


def derive_context(transcript: str, label: str, unit_index: int) -> Context:
    """Build a Context for the unit at `unit_index` from the known transcript.

    Falls back to an isolated, neighborless context if the index is out of range
    (caller flags the candidate needs_review in that case).
    """
    units = _unit_map(transcript)
    if unit_index < 0 or unit_index >= len(units):
        return Context(source_word=label, position_in_word=PositionInWord.isolated)

    _, word_index, pos, word_len = units[unit_index]
    source_word = transcript.split()[word_index]

    if word_len == 1:
        position = PositionInWord.isolated
    elif pos == 0:
        position = PositionInWord.initial
    elif pos == word_len - 1:
        position = PositionInWord.final
    else:
        position = PositionInWord.medial

    left = units[unit_index - 1] if pos > 0 else None
    right = units[unit_index + 1] if pos < word_len - 1 else None
    return Context(
        source_word=source_word,
        left_neighbor=left[0] if left else None,
        right_neighbor=right[0] if right else None,
        position_in_word=position,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python -m pytest tests/test_segment_context.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/segment.py packages/ingest-segment/tests/test_segment_context.py
git commit -m "feat(ingest): derive Context from the known transcript"
```

---

### Task 15: vision result schema + Claude client wrapper

**Files:**
- Modify: `packages/ingest-segment/src/ingest_segment/segment.py`
- Test: `packages/ingest-segment/tests/test_segment_vision.py`

Define the structured-output schema (`VisionBox`, `VisionResult`) and `ClaudeVisionClient` wrapping the Anthropic SDK. `segment_region` takes a `vision` callable so unit tests pass a mock and never touch the network. The real client uses `client.messages.parse` with `output_config={"format": ...}`, Claude Opus 4.8, vision (base64 PNG), adaptive thinking, `effort: high`.

- [ ] **Step 1: Write the failing test**

Create `packages/ingest-segment/tests/test_segment_vision.py`:

```python
from ingest_segment.segment import VisionBox, VisionResult, build_region_prompt


def test_vision_result_schema_validates():
    vr = VisionResult(
        boxes=[
            VisionBox(label="c", kind="single", x=0, y=0, w=10, h=20, confidence=0.9),
            VisionBox(label="at", kind="ligature", x=10, y=0, w=18, h=20, confidence=0.4),
        ]
    )
    assert VisionResult.model_validate_json(vr.model_dump_json()) == vr


def test_prompt_includes_transcript_and_units():
    prompt = build_region_prompt(transcript="the cat", expected_units=["t", "h", "e", "c", "a", "t"])
    assert "the cat" in prompt
    assert "crop pixel coordinates" in prompt.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python -m pytest tests/test_segment_vision.py -q`
Expected: FAIL with `ImportError: cannot import name 'VisionBox'`

- [ ] **Step 3: Implement the schema, prompt, and client**

Append to `packages/ingest-segment/src/ingest_segment/segment.py`:

```python
import base64

from pydantic import BaseModel, Field

from hwfont_schema import Region

VISION_MODEL = "claude-opus-4-8"


class VisionBox(BaseModel):
    """One labeled unit located by the vision model, in crop pixel coordinates."""

    label: str
    kind: str  # "single" | "ligature"
    x: float
    y: float
    w: float = Field(gt=0)
    h: float = Field(gt=0)
    confidence: float = Field(ge=0.0, le=1.0)


class VisionResult(BaseModel):
    """The structured vision response: labeled boxes ordered left-to-right."""

    boxes: list[VisionBox] = Field(default_factory=list)


def build_region_prompt(transcript: str, expected_units: list[str]) -> str:
    """Prompt for one region: the writer copied a known line; locate each unit."""
    units = " ".join(expected_units)
    return (
        "This image crop is one ruled row from a handwriting-capture page. "
        f"The writer was asked to copy this exact text: {transcript!r}\n"
        f"Expected units to locate, in order: {units}\n"
        "Locate and label each handwritten unit you were asked to capture. "
        "Return boxes in crop pixel coordinates (origin top-left), ordered left-to-right. "
        "Use kind 'single' for one glyph and 'ligature' for a multi-character cluster. "
        "Set confidence in [0,1] reflecting how clearly you can locate the unit."
    )


class ClaudeVisionClient:
    """Wraps the Anthropic SDK to return a validated VisionResult for a region crop."""

    def __init__(self, client, model: str = VISION_MODEL) -> None:
        self._client = client
        self.model = model

    def __call__(self, crop_png: bytes, region: Region) -> VisionResult:
        b64 = base64.standard_b64encode(crop_png).decode("ascii")
        response = self._client.messages.parse(
            model=self.model,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            output_format=VisionResult,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": b64},
                        },
                        {
                            "type": "text",
                            "text": build_region_prompt(region.expected_transcript, region.expected_units),
                        },
                    ],
                }
            ],
        )
        return response.parsed_output
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python -m pytest tests/test_segment_vision.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/segment.py packages/ingest-segment/tests/test_segment_vision.py
git commit -m "feat(ingest): vision result schema, prompt, and Claude client wrapper"
```

---

### Task 16: crop, map boxes onto strokes, build candidates

**Files:**
- Modify: `packages/ingest-segment/src/ingest_segment/segment.py`
- Test: `packages/ingest-segment/tests/test_segment_region.py`

`segment_region(...)` is the per-region core: crop the aligned raster to the region bbox, call the (injected) vision function, map each crop-space box to page-px, assign aligned strokes that fall mostly inside the box, derive context, and flag `needs_review` on box/unit count mismatch or low confidence. It returns a list of `(Candidate, assigned_strokes)` tuples; writing files is Task 18's job.

- [ ] **Step 1: Write the failing test**

Create `packages/ingest-segment/tests/test_segment_region.py`:

```python
from PIL import Image

from hwfont_schema import BBox, CandidateStatus, Region
from ingest_segment.segment import VisionBox, VisionResult, segment_region


def _region():
    return Region(
        id="p0-r0",
        expected_transcript="cat",
        baseline_y=60.0,
        bbox=BBox(x=10.0, y=20.0, w=120.0, h=40.0),
        expected_units=["c", "a", "t"],
    )


def _raster():
    return Image.new("L", (200, 200), color=255)


def _vision_three_boxes(crop_png, region):
    # three single glyphs left-to-right within the crop (120x40)
    return VisionResult(
        boxes=[
            VisionBox(label="c", kind="single", x=0, y=0, w=30, h=40, confidence=0.9),
            VisionBox(label="a", kind="single", x=40, y=0, w=30, h=40, confidence=0.85),
            VisionBox(label="t", kind="single", x=80, y=0, w=30, h=40, confidence=0.8),
        ]
    )


def test_segment_region_builds_candidates_with_pagepx_bbox():
    region = _region()
    # one stroke clearly inside the first box (page-px ~ x in [10,40], y in [20,60])
    strokes = [[(15.0, 30.0), (20.0, 40.0), (25.0, 35.0)]]
    results = segment_region(
        region=region,
        raster=_raster(),
        page_strokes=strokes,
        vision=_vision_three_boxes,
        page_id="p0",
        alignment_method="fiducial",
        page_low_confidence=False,
        model="claude-opus-4-8",
        created_at="2026-06-22T00:00:00Z",
    )
    assert len(results) == 3
    first_cand, first_strokes = results[0]
    assert first_cand.label == "c"
    # crop box x=0 -> page-px x=region.bbox.x (10)
    assert abs(first_cand.bbox.x - 10.0) < 1e-6
    assert abs(first_cand.bbox.y - 20.0) < 1e-6
    assert first_cand.context.source_word == "cat"
    assert first_cand.status == CandidateStatus.pending
    assert len(first_strokes) == 1  # the inside stroke went to box 'c'
    assert len(results[1][1]) == 0  # no strokes for 'a'


def test_segment_region_flags_count_mismatch():
    region = _region()

    def vision_two(crop_png, r):
        return VisionResult(
            boxes=[
                VisionBox(label="c", kind="single", x=0, y=0, w=30, h=40, confidence=0.9),
                VisionBox(label="a", kind="single", x=40, y=0, w=30, h=40, confidence=0.9),
            ]
        )

    results = segment_region(
        region=region, raster=_raster(), page_strokes=[], vision=vision_two,
        page_id="p0", alignment_method="fiducial", page_low_confidence=False,
        model="claude-opus-4-8", created_at="2026-06-22T00:00:00Z",
    )
    # 2 boxes vs 3 expected units -> all flagged needs_review
    assert all(c.status == CandidateStatus.needs_review for c, _ in results)


def test_segment_region_flags_low_confidence_box_and_page():
    region = _region()

    def vision_lowconf(crop_png, r):
        return VisionResult(
            boxes=[
                VisionBox(label="c", kind="single", x=0, y=0, w=30, h=40, confidence=0.2),
                VisionBox(label="a", kind="single", x=40, y=0, w=30, h=40, confidence=0.9),
                VisionBox(label="t", kind="single", x=80, y=0, w=30, h=40, confidence=0.9),
            ]
        )

    results = segment_region(
        region=region, raster=_raster(), page_strokes=[], vision=vision_lowconf,
        page_id="p0", alignment_method="geometric_scale", page_low_confidence=True,
        model="claude-opus-4-8", created_at="2026-06-22T00:00:00Z",
    )
    by_label = {c.label: c for c, _ in results}
    assert by_label["c"].status == CandidateStatus.needs_review  # low confidence box
    assert by_label["a"].status == CandidateStatus.needs_review  # page low confidence
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python -m pytest tests/test_segment_region.py -q`
Expected: FAIL with `ImportError: cannot import name 'segment_region'`

- [ ] **Step 3: Implement `segment_region`**

Append to `packages/ingest-segment/src/ingest_segment/segment.py`. Add the needed imports at the top of the file (merge with the existing `from hwfont_schema import ...` line):

```python
from typing import Callable

from PIL import Image

from hwfont_schema import BBox, Candidate, CandidateStatus, Kind

# a box's confidence below this flags its candidate needs_review
_LOW_CONFIDENCE = 0.5
# a stroke is assigned to a box if at least this fraction of its points fall inside
_STROKE_INSIDE_FRACTION = 0.5

VisionFn = Callable[[bytes, Region], VisionResult]


def _crop_png(raster: Image.Image, bbox: BBox) -> bytes:
    import io

    left, top = int(bbox.x), int(bbox.y)
    right, bottom = int(bbox.x + bbox.w), int(bbox.y + bbox.h)
    crop = raster.crop((left, top, right, bottom))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


def _points_in_box(points: list[tuple[float, float]], box: BBox) -> float:
    if not points:
        return 0.0
    inside = sum(
        1 for x, y in points if box.x <= x <= box.x + box.w and box.y <= y <= box.y + box.h
    )
    return inside / len(points)


def segment_region(
    region: Region,
    raster: Image.Image,
    page_strokes: list[list[tuple[float, float]]],
    vision: VisionFn,
    page_id: str,
    alignment_method: str,
    page_low_confidence: bool,
    model: str,
    created_at: str,
) -> list[tuple[Candidate, list[list[tuple[float, float]]]]]:
    """Locate, label, and box each unit in one region; map strokes; build candidates.

    `page_strokes` are aligned ink strokes in page-pixel space. Returns
    (candidate, assigned_strokes) pairs; the caller writes stroke/crop files.
    """
    crop = _crop_png(raster, region.bbox)
    result = vision(crop, region)

    count_mismatch = len(result.boxes) != len(region.expected_units)

    out: list[tuple[Candidate, list[list[tuple[float, float]]]]] = []
    for i, box in enumerate(result.boxes):
        # crop px -> page px: offset by the region crop origin
        page_bbox = BBox(
            x=region.bbox.x + box.x,
            y=region.bbox.y + box.y,
            w=box.w,
            h=box.h,
        )
        assigned = [
            s for s in page_strokes if _points_in_box(s, page_bbox) >= _STROKE_INSIDE_FRACTION
        ]
        context = derive_context(region.expected_transcript, box.label, i)

        needs_review = (
            count_mismatch
            or page_low_confidence
            or box.confidence < _LOW_CONFIDENCE
        )
        status = CandidateStatus.needs_review if needs_review else CandidateStatus.pending

        candidate = Candidate(
            id=f"{region.id}-{i}",
            page_id=page_id,
            region_id=region.id,
            label=box.label,
            kind=Kind(box.kind),
            confidence=box.confidence,
            bbox=page_bbox,
            context=context,
            status=status,
            alignment_method=alignment_method,
            model=model,
            created_at=created_at,
        )
        out.append((candidate, assigned))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python -m pytest tests/test_segment_region.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/segment.py packages/ingest-segment/tests/test_segment_region.py
git commit -m "feat(ingest): segment a region into candidates with stroke assignment + flags"
```

---

## Phase 8 — `candidates_out`: write/read the CandidateSet

### Task 17: write and read a CandidateSet directory

**Files:**
- Create: `packages/ingest-segment/src/ingest_segment/candidates_out.py`
- Test: `packages/ingest-segment/tests/test_candidates_out.py`

`write_candidate_set(out_dir, provenance, items, ..., force)` writes `candidates.json`, `strokes/<id>.json` (a `hwfont_schema.StrokeData`), and `crop/<id>.png`, sets each candidate's `strokes_path`/`crop_path`, sorts candidates lowest-confidence-first, and refuses to overwrite without `force`. `read_candidate_set(out_dir)` parses `candidates.json` back into a `CandidateSet`.

- [ ] **Step 1: Write the failing test**

Create `packages/ingest-segment/tests/test_candidates_out.py`:

```python
import json

import pytest

from hwfont_schema import (
    BBox,
    Candidate,
    CandidateProvenance,
    CandidateStatus,
    Context,
    Kind,
    PositionInWord,
)
from ingest_segment.candidates_out import read_candidate_set, write_candidate_set


def _cand(cid, conf):
    return Candidate(
        id=cid, page_id="p0", region_id="p0-r0", label="a", kind=Kind.single,
        confidence=conf, bbox=BBox(x=0, y=0, w=10, h=10),
        context=Context(source_word="a", position_in_word=PositionInWord.isolated),
        status=CandidateStatus.pending, alignment_method="fiducial",
        model="claude-opus-4-8", created_at="2026-06-22T00:00:00Z",
    )


def _prov():
    return CandidateProvenance(
        source_page_id="p0", source_raster="page0.png",
        alignment_method="fiducial", model="claude-opus-4-8",
    )


def test_write_sorts_low_confidence_first_and_writes_sidecars(tmp_path):
    out = tmp_path / "candidates"
    items = [
        (_cand("hi", 0.9), [[(1.0, 1.0), (2.0, 2.0)]]),
        (_cand("lo", 0.2), []),  # no strokes
    ]
    cs = write_candidate_set(out, _prov(), items, crops={"hi": b"\x89PNG-hi", "lo": b"\x89PNG-lo"})

    assert [c.id for c in cs.candidates] == ["lo", "hi"]  # lowest confidence first
    hi = next(c for c in cs.candidates if c.id == "hi")
    assert hi.strokes_path == "strokes/hi.json"
    assert hi.crop_path == "crop/hi.png"
    lo = next(c for c in cs.candidates if c.id == "lo")
    assert lo.strokes_path is None  # no strokes assigned
    assert (out / "candidates.json").exists()
    assert (out / "strokes" / "hi.json").exists()
    assert (out / "crop" / "lo.png").read_bytes() == b"\x89PNG-lo"


def test_read_roundtrips(tmp_path):
    out = tmp_path / "candidates"
    items = [(_cand("c1", 0.5), [])]
    written = write_candidate_set(out, _prov(), items, crops={"c1": b"x"})
    loaded = read_candidate_set(out)
    assert loaded == written


def test_refuses_overwrite_without_force(tmp_path):
    out = tmp_path / "candidates"
    write_candidate_set(out, _prov(), [(_cand("c1", 0.5), [])], crops={"c1": b"x"})
    with pytest.raises(FileExistsError):
        write_candidate_set(out, _prov(), [(_cand("c1", 0.5), [])], crops={"c1": b"x"})
    # force overwrites
    write_candidate_set(out, _prov(), [(_cand("c1", 0.5), [])], crops={"c1": b"x"}, force=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python -m pytest tests/test_candidates_out.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest_segment.candidates_out'`

- [ ] **Step 3: Implement**

Create `packages/ingest-segment/src/ingest_segment/candidates_out.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python -m pytest tests/test_candidates_out.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/candidates_out.py packages/ingest-segment/tests/test_candidates_out.py
git commit -m "feat(ingest): write/read CandidateSet manifest + per-candidate sidecars"
```

---

## Phase 9 — `run`: orchestrate the pipeline + CLI

### Task 18: orchestration function

**Files:**
- Create: `packages/ingest-segment/src/ingest_segment/run.py`
- Test: `packages/ingest-segment/tests/test_run.py`

`ingest_page(sidecar_page, raster, svg_path, vision, model, created_at, out_dir, force)` wires the modules: parse+separate ink (if SVG), detect fiducials, align, transform strokes to page-px, segment each region, collect crops, write the set. The vision function is injected so this is fully testable offline.

- [ ] **Step 1: Write the failing test**

Create `packages/ingest-segment/tests/test_run.py`:

```python
from PIL import Image, ImageDraw

from hwfont_schema import BBox, Fiducial, Page, Region
from ingest_segment.run import ingest_page
from ingest_segment.segment import VisionBox, VisionResult


def _page():
    return Page(
        id="p0", width_px=200, height_px=200, dpi=226,
        fiducials=[
            Fiducial(id="tl", x=20, y=20),
            Fiducial(id="tr", x=180, y=20),
            Fiducial(id="bl", x=20, y=180),
            Fiducial(id="br", x=180, y=180),
        ],
        regions=[
            Region(
                id="p0-r0", expected_transcript="hi", baseline_y=110.0,
                bbox=BBox(x=40.0, y=80.0, w=120.0, h=40.0),
                expected_units=["h", "i"],
            )
        ],
    )


def _raster_with_fiducials():
    img = Image.new("L", (200, 200), color=255)
    draw = ImageDraw.Draw(img)
    for x, y in [(20, 20), (180, 20), (20, 180), (180, 180)]:
        draw.ellipse([x - 8, y - 8, x + 8, y + 8], fill=0)
    return img


def _vision(crop_png, region):
    return VisionResult(
        boxes=[
            VisionBox(label="h", kind="single", x=0, y=0, w=50, h=40, confidence=0.9),
            VisionBox(label="i", kind="single", x=60, y=0, w=40, h=40, confidence=0.85),
        ]
    )


def test_ingest_page_emits_candidate_set(tmp_path):
    out = tmp_path / "out"
    cs = ingest_page(
        page=_page(),
        raster=_raster_with_fiducials(),
        export_size=(200, 200),
        page_strokes_export=[[(45.0, 95.0), (45.0, 110.0)]],  # inside region/box 'h'
        vision=_vision,
        model="claude-opus-4-8",
        created_at="2026-06-22T00:00:00Z",
        out_dir=out,
    )
    assert cs.provenance.alignment_method == "fiducial"
    assert {c.label for c in cs.candidates} == {"h", "i"}
    # lowest confidence first
    assert cs.candidates[0].confidence <= cs.candidates[-1].confidence
    # the 'h' candidate got the stroke (identity export -> page px unchanged)
    h = next(c for c in cs.candidates if c.label == "h")
    assert h.strokes_path is not None
    assert (out / "candidates.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python -m pytest tests/test_run.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'ingest_segment.run'`

- [ ] **Step 3: Implement orchestration**

Create `packages/ingest-segment/src/ingest_segment/run.py`:

```python
from __future__ import annotations

from pathlib import Path

from PIL import Image

from hwfont_schema import CandidateProvenance, CandidateSet, Page
from ingest_segment.align import apply_affine, align_page
from ingest_segment.candidates_out import write_candidate_set
from ingest_segment.raster import detect_fiducials
from ingest_segment.segment import VisionFn, _crop_png, segment_region


def ingest_page(
    page: Page,
    raster: Image.Image,
    export_size: tuple[int, int],
    page_strokes_export: list[list[tuple[float, float]]],
    vision: VisionFn,
    model: str,
    created_at: str,
    out_dir: str | Path,
    source_raster: str = "page.png",
    source_svg: str | None = None,
    force: bool = False,
) -> CandidateSet:
    """Run the full pipeline for one page; emit a CandidateSet directory.

    `page_strokes_export` are ink strokes in the SVG/export coordinate space; they
    are transformed into page-pixel space by the chosen alignment.
    """
    measured = detect_fiducials(raster, page.fiducials)
    alignment = align_page(measured, page, export_size)
    page_strokes = [apply_affine(alignment.matrix, s) for s in page_strokes_export]

    items: list = []
    crops: dict[str, bytes] = {}
    for region in page.regions:
        region_items = segment_region(
            region=region,
            raster=raster,
            page_strokes=page_strokes,
            vision=vision,
            page_id=page.id,
            alignment_method=alignment.method,
            page_low_confidence=alignment.low_confidence,
            model=model,
            created_at=created_at,
        )
        for candidate, strokes in region_items:
            crops[candidate.id] = _crop_png(raster, candidate.bbox)
            items.append((candidate, strokes))

    provenance = CandidateProvenance(
        source_page_id=page.id,
        source_raster=source_raster,
        source_svg=source_svg,
        alignment_method=alignment.method,
        alignment_residual_px=alignment.residual_px,
        model=model,
    )
    return write_candidate_set(out_dir, provenance, items, crops=crops, force=force)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python -m pytest tests/test_run.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/run.py packages/ingest-segment/tests/test_run.py
git commit -m "feat(ingest): orchestrate parse->align->segment->emit for one page"
```

---

### Task 19: CLI entry point

**Files:**
- Modify: `packages/ingest-segment/src/ingest_segment/run.py`
- Test: `packages/ingest-segment/tests/test_cli.py`

`main(argv)` parses args (`--raster`, `--sidecar`, optional `--svg`, `--out`, `--force`, `--page-index`), loads the sidecar (validated against `hwfont_schema.CaptureSidecar`), loads the raster, parses+separates ink if an SVG is given, builds a real `ClaudeVisionClient` (reads `ANTHROPIC_API_KEY` via the SDK), runs `ingest_page`, and prints a summary ordered lowest-confidence-first. The vision client construction is isolated in a tiny factory so the CLI test can run without the network by patching it.

- [ ] **Step 1: Write the failing test**

Create `packages/ingest-segment/tests/test_cli.py`:

```python
from PIL import Image, ImageDraw

import ingest_segment.run as run_mod
from hwfont_schema import BBox, CaptureSidecar, Fiducial, Page, Region
from ingest_segment.run import main
from ingest_segment.segment import VisionBox, VisionResult


def _write_inputs(tmp_path):
    page = Page(
        id="p0", width_px=200, height_px=200, dpi=226,
        fiducials=[
            Fiducial(id="tl", x=20, y=20), Fiducial(id="tr", x=180, y=20),
            Fiducial(id="bl", x=20, y=180), Fiducial(id="br", x=180, y=180),
        ],
        regions=[Region(
            id="p0-r0", expected_transcript="hi", baseline_y=110.0,
            bbox=BBox(x=40.0, y=80.0, w=120.0, h=40.0), expected_units=["h", "i"],
        )],
    )
    sidecar = CaptureSidecar(pages=[page])
    (tmp_path / "capture.sidecar.json").write_text(sidecar.model_dump_json(), encoding="utf-8")

    img = Image.new("L", (200, 200), color=255)
    draw = ImageDraw.Draw(img)
    for x, y in [(20, 20), (180, 20), (20, 180), (180, 180)]:
        draw.ellipse([x - 8, y - 8, x + 8, y + 8], fill=0)
    img.save(tmp_path / "page0.png")


def test_cli_runs_with_mocked_vision(tmp_path, monkeypatch, capsys):
    _write_inputs(tmp_path)

    def fake_vision(crop_png, region):
        return VisionResult(boxes=[
            VisionBox(label="h", kind="single", x=0, y=0, w=50, h=40, confidence=0.9),
            VisionBox(label="i", kind="single", x=60, y=0, w=40, h=40, confidence=0.3),
        ])

    monkeypatch.setattr(run_mod, "_build_vision_client", lambda model: fake_vision)

    out = tmp_path / "out"
    rc = main([
        "--raster", str(tmp_path / "page0.png"),
        "--sidecar", str(tmp_path / "capture.sidecar.json"),
        "--out", str(out),
    ])
    assert rc == 0
    assert (out / "candidates.json").exists()
    printed = capsys.readouterr().out
    assert "2 candidate" in printed  # summary line
    # lowest-confidence-first ordering surfaced 'i' (0.3) before 'h' (0.9)
    assert printed.index("needs_review") < printed.index("pending")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd packages/ingest-segment && python -m pytest tests/test_cli.py -q`
Expected: FAIL with `ImportError: cannot import name 'main'`

- [ ] **Step 3: Implement the CLI**

Append to `packages/ingest-segment/src/ingest_segment/run.py`. Add these imports at the top (merge with existing):

```python
import argparse

from hwfont_schema import CaptureSidecar
from ingest_segment.raster import load_raster
from ingest_segment.segment import ClaudeVisionClient, VISION_MODEL
from ingest_segment.svg_strokes import parse_svg_strokes, separate_ink
```

Then append:

```python
def _build_vision_client(model: str) -> VisionFn:
    """Construct the real Claude vision client (reads ANTHROPIC_API_KEY via the SDK)."""
    import anthropic

    return ClaudeVisionClient(anthropic.Anthropic(), model=model)


def _ink_strokes_from_svg(svg_path: str) -> list[list[tuple[float, float]]]:
    ink = separate_ink(parse_svg_strokes(svg_path))
    return [s.points for s in ink]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Segment a written-on capture page into candidate glyph samples."
    )
    parser.add_argument("--raster", required=True, help="page raster PNG (required)")
    parser.add_argument("--sidecar", required=True, help="capture.sidecar.json (Contract X)")
    parser.add_argument("--svg", default=None, help="optional SVG ink export")
    parser.add_argument("--out", required=True, help="output CandidateSet directory")
    parser.add_argument("--page-index", type=int, default=0, help="page in the sidecar (default: 0)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing output dir")
    parser.add_argument("--model", default=VISION_MODEL, help=f"vision model (default: {VISION_MODEL})")
    parser.add_argument("--created-at", default=None, help="ISO timestamp stamped on candidates")
    args = parser.parse_args(argv)

    sidecar = CaptureSidecar.model_validate_json(Path(args.sidecar).read_text(encoding="utf-8"))
    if args.page_index < 0 or args.page_index >= len(sidecar.pages):
        print(f"page index {args.page_index} out of range (sidecar has {len(sidecar.pages)} pages)")
        return 1
    page = sidecar.pages[args.page_index]

    raster = load_raster(args.raster)
    if raster.size != (page.width_px, page.height_px):
        print(
            f"raster size {raster.size} != sidecar page size "
            f"({page.width_px}, {page.height_px})"
        )
        return 1

    if args.svg:
        strokes_export = _ink_strokes_from_svg(args.svg)
        export_size = raster.size  # SVG ink already in raster-pixel space when paired with this raster
    else:
        strokes_export = []
        export_size = raster.size

    created_at = args.created_at or "1970-01-01T00:00:00Z"
    cs = ingest_page(
        page=page,
        raster=raster,
        export_size=export_size,
        page_strokes_export=strokes_export,
        vision=_build_vision_client(args.model),
        model=args.model,
        created_at=created_at,
        out_dir=args.out,
        source_raster=Path(args.raster).name,
        source_svg=Path(args.svg).name if args.svg else None,
        force=args.force,
    )

    print(
        f"Wrote {len(cs.candidates)} candidate(s) to {args.out} "
        f"(alignment: {cs.provenance.alignment_method})."
    )
    for c in cs.candidates:  # already sorted lowest-confidence-first
        print(f"  {c.confidence:.2f}  {c.status.value:13s}  {c.label!r} ({c.kind.value})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd packages/ingest-segment && python -m pytest tests/test_cli.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/run.py packages/ingest-segment/tests/test_cli.py
git commit -m "feat(ingest): add ingest-segment CLI entry point"
```

---

## Phase 10 — end-to-end + integration

### Task 20: end-to-end test from a real capture-template page

**Files:**
- Test: `packages/ingest-segment/tests/test_end_to_end.py`

Generate a real `capture-template` page (PDF + sidecar) with fiducials, rasterize *one page* to PNG (the fiducial dots are detectable), synthesize ink strokes inside a region, run `ingest_page` with mocked vision, and assert the emitted `CandidateSet` is valid, ordered, and carries context/strokes/flags. This proves the two packages interoperate through Contract X.

Rasterizing the reportlab PDF needs a renderer. To keep the test offline and dependency-light, **do not** render the PDF; instead build the page raster directly from the sidecar's fiducial positions (the same dots reportlab would draw), which is exactly what `detect_fiducials` consumes. This still exercises the real `capture-template` sidecar geometry.

- [ ] **Step 1: Write the test**

Create `packages/ingest-segment/tests/test_end_to_end.py`:

```python
from PIL import Image, ImageDraw

from capture_template.layout import LayoutModel, LayoutPage, PageConfig, build_layout
from capture_template.sidecar_out import build_sidecar
from capture_template.targets import default_targets
from ingest_segment.run import ingest_page
from ingest_segment.segment import VisionBox, VisionResult


def _small_config():
    return PageConfig(
        width_px=600, height_px=400, dpi=150, margin_px=40,
        prompt_font_px=18, prompt_gap_px=8, line_height_px=40,
        row_pitch_px=90, max_line_chars=40, fiducial_inset_px=30, fiducial_radius_px=8,
    )


def _raster_from_sidecar_page(page, radius=8):
    img = Image.new("L", (page.width_px, page.height_px), color=255)
    draw = ImageDraw.Draw(img)
    for f in page.fiducials:
        draw.ellipse([f.x - radius, f.y - radius, f.x + radius, f.y + radius], fill=0)
    return img


def test_capture_template_to_candidate_set(tmp_path):
    cfg = _small_config()
    # one real prompt line through the real layout + sidecar path
    from capture_template.planner import PromptLine

    model = build_layout([PromptLine(text="cat", is_drill=False)], default_targets(), cfg)
    sidecar = build_sidecar(model)
    page = sidecar.pages[0]
    assert len(page.fiducials) == 4  # fiducials flowed through Contract X

    raster = _raster_from_sidecar_page(page)
    region = page.regions[0]

    # synthesize one ink stroke inside the left third of the region
    bx, by, bw, bh = region.bbox.x, region.bbox.y, region.bbox.w, region.bbox.h
    stroke = [(bx + 5, by + 5), (bx + 8, by + bh - 5), (bx + 12, by + 10)]

    def vision(crop_png, r):
        third = bw / 3.0
        return VisionResult(boxes=[
            VisionBox(label="c", kind="single", x=0, y=0, w=third, h=bh, confidence=0.9),
            VisionBox(label="a", kind="single", x=third, y=0, w=third, h=bh, confidence=0.2),
            VisionBox(label="t", kind="single", x=2 * third, y=0, w=third, h=bh, confidence=0.8),
        ])

    out = tmp_path / "out"
    cs = ingest_page(
        page=page, raster=raster, export_size=raster.size,
        page_strokes_export=[stroke], vision=vision,
        model="claude-opus-4-8", created_at="2026-06-22T00:00:00Z", out_dir=out,
    )

    assert cs.provenance.alignment_method == "fiducial"
    assert cs.provenance.alignment_residual_px is not None
    # lowest-confidence-first
    confs = [c.confidence for c in cs.candidates]
    assert confs == sorted(confs)
    # low-confidence box flagged
    a = next(c for c in cs.candidates if c.label == "a")
    assert a.status.value == "needs_review"
    # context derived from the known transcript
    c0 = next(c for c in cs.candidates if c.label == "c")
    assert c0.context.source_word == "cat"
    # the synthesized stroke landed in the 'c' candidate
    assert c0.strokes_path is not None
```

- [ ] **Step 2: Install all three packages editable + run the test**

Run: `pip install -e packages/hwfont-schema -e packages/capture-template -e packages/ingest-segment && cd packages/ingest-segment && python -m pytest tests/test_end_to_end.py -q`
Expected: PASS

If `default_targets()` produces a target set that can't appear in a 3-char `"cat"` line, the layout still builds (targets only constrain `ligature_targets`/`expected_units` derivation, not whether the line is accepted) — `build_layout` does not validate coverage. If `PromptLine` has additional required fields, inspect `packages/capture-template/src/capture_template/planner.py` and supply them.

- [ ] **Step 3: Commit**

```bash
git add packages/ingest-segment/tests/test_end_to_end.py
git commit -m "test(ingest): end-to-end capture-template -> CandidateSet via Contract X"
```

---

### Task 21: env-gated real-API integration test

**Files:**
- Test: `packages/ingest-segment/tests/test_integration_vision.py`

One opt-in test that hits the real Claude Opus 4.8 vision API. Skipped unless `ANTHROPIC_API_KEY` is set, so the suite stays offline by default.

- [ ] **Step 1: Write the test**

Create `packages/ingest-segment/tests/test_integration_vision.py`:

```python
import os

import pytest
from PIL import Image, ImageDraw

from hwfont_schema import BBox, Region
from ingest_segment.segment import ClaudeVisionClient

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; skipping real-API integration test",
)


def _crop_png_with_text():
    img = Image.new("L", (240, 80), color=255)
    draw = ImageDraw.Draw(img)
    draw.text((10, 25), "cat", fill=0)
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_real_vision_returns_boxes():
    import anthropic

    client = ClaudeVisionClient(anthropic.Anthropic())
    region = Region(
        id="p0-r0", expected_transcript="cat", baseline_y=60.0,
        bbox=BBox(x=0, y=0, w=240, h=80), expected_units=["c", "a", "t"],
    )
    result = client(_crop_png_with_text(), region)
    assert len(result.boxes) >= 1
    for box in result.boxes:
        assert 0.0 <= box.confidence <= 1.0
        assert box.w > 0 and box.h > 0
```

- [ ] **Step 2: Verify it skips without a key**

Run: `cd packages/ingest-segment && python -m pytest tests/test_integration_vision.py -q`
Expected: `1 skipped` (no `ANTHROPIC_API_KEY` in the test environment)

- [ ] **Step 3: (Optional, manual) run against the real API**

Run: `cd packages/ingest-segment && ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY python -m pytest tests/test_integration_vision.py -q`
Expected: PASS (consumes real API credits — run only when validating the vision path)

- [ ] **Step 4: Commit**

```bash
git add packages/ingest-segment/tests/test_integration_vision.py
git commit -m "test(ingest): env-gated real Opus 4.8 vision integration test"
```

---

### Task 22: full-suite green + module exports

**Files:**
- Modify: `packages/ingest-segment/src/ingest_segment/__init__.py`

Surface the package's public API and confirm everything passes together.

- [ ] **Step 1: Update exports**

Replace `packages/ingest-segment/src/ingest_segment/__init__.py` with:

```python
from ingest_segment.align import Alignment, align_page, apply_affine, estimate_affine, residual
from ingest_segment.candidates_out import read_candidate_set, write_candidate_set
from ingest_segment.raster import detect_fiducials, load_raster
from ingest_segment.run import ingest_page, main
from ingest_segment.segment import (
    ClaudeVisionClient,
    VisionBox,
    VisionResult,
    derive_context,
    segment_region,
)
from ingest_segment.svg_strokes import RawStroke, parse_svg_strokes, separate_ink

__version__ = "0.1.0"

__all__ = [
    "Alignment",
    "align_page",
    "apply_affine",
    "estimate_affine",
    "residual",
    "read_candidate_set",
    "write_candidate_set",
    "detect_fiducials",
    "load_raster",
    "ingest_page",
    "main",
    "ClaudeVisionClient",
    "VisionBox",
    "VisionResult",
    "derive_context",
    "segment_region",
    "RawStroke",
    "parse_svg_strokes",
    "separate_ink",
    "__version__",
]
```

- [ ] **Step 2: Run the whole repo's tests**

Run: `pip install -e packages/hwfont-schema -e packages/capture-template -e packages/ingest-segment && for p in hwfont-schema capture-template ingest-segment; do (cd packages/$p && python -m pytest -q) || exit 1; done`
Expected: all three suites PASS (the integration test shows as skipped)

- [ ] **Step 3: Commit**

```bash
git add packages/ingest-segment/src/ingest_segment/__init__.py
git commit -m "feat(ingest): export public API surface"
```

---

## Self-Review notes (verified against the spec)

- **Precursors** (spec §Precursors): `Candidate`/`CandidateSet` contract → Tasks 1–3; `Page.fiducials` → Task 4; `capture-template` prints + records fiducials → Tasks 5–7. ✅
- **Architecture files** (spec §Architecture): `svg_strokes` → 9–10, `raster` → 11, `align` → 12–13, `segment` → 14–16, `candidates_out` → 17, `run` + CLI → 18–19. ✅
- **Data flow** (spec): parse ink → load raster → align → per-region crop+vision+map+context → CandidateSet, ordered lowest-confidence-first. Realized in `ingest_page` (Task 18) and asserted end-to-end (Task 20). ✅
- **Candidate contract fields** (spec §The Candidate Contract): all fields present in Task 2; `CandidateStatus` enum with `pending`/`needs_review` in Task 1; reuses `Context`/`BBox`/`Kind`/`StrokeData`. ✅
- **Alignment** (spec §Alignment): fiducial affine + geometric-scale fallback + residual-threshold `needs_review` flagging + provenance recording. Tasks 12–13, 18. ✅
- **Segmentation** (spec §Segmentation): crop → Claude Opus 4.8 (adaptive thinking, effort high, structured output) → validate → crop→page-px map → stroke assignment (per-box overlap-fraction threshold — see note below) → transcript-derived context → flag count-mismatch / low-confidence / zero-box. Tasks 14–16. Vision **mocked** in unit tests; **one env-gated integration test** (Task 21). ✅
  - *Simplification carried from the design spec:* the implemented stroke assignment is **independent per-box** (a stroke is assigned to every box it overlaps ≥ threshold), not the spec's single majority-overlap-box rule, and straddling/unassigned strokes are **not** separately flagged. This is acceptable for non-overlapping box layouts and is masked by `needs_review` flagging; it is isolated behind `segment_region` and is one of the things to revisit against a real device export (alongside ink/template separation and the export coordinate-space assumption — see Known Risk).
- **Error handling** (spec §Error Handling): raster size mismatch → CLI error (Task 19); sidecar validated via `model_validate_json` (Task 19); fiducials-not-found → geometric-scale fallback + low-confidence (Task 13); output dir overwrite refused without `--force` (Tasks 17, 19). Note: "raster missing" surfaces as Pillow's `FileNotFoundError` from `load_raster`; "SVG parse failure → raster-only" is handled by simply omitting `--svg`. ✅
- **Type consistency:** `Alignment.matrix`/`apply_affine` (2×3 ndarray), `VisionFn` signature `(bytes, Region) -> VisionResult`, `segment_region` returns `(Candidate, strokes)` consumed identically in `ingest_page` and `write_candidate_set`. Confidence bound `[0,1]` consistent across `VisionBox` and `Candidate`. ✅

## Known follow-ups (explicitly out of scope per spec)

- `review` (human-in-the-loop consumer of the `CandidateSet`) — its own later cycle.
- Em-space normalization and writing `Sample`s to the glyph store — happens in `review` on accept.
- Native `.rm` parsing; rendering the reportlab PDF to a raster (the end-to-end test builds the raster from sidecar geometry instead). The CLI consumes whatever PNG the user exports from the device.
- The `svg_strokes` ink/template separation and the export coordinate-space assumption are the first things to revisit against a real reMarkable export (spec §Known Risk); they are isolated behind clean interfaces for exactly this reason.

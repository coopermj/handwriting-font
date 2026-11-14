"""Load accepted samples from a GlyphStore: sidecars, em-v1 check, kind check, shaping position, digest."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from hwfont_schema import (
    Context,
    CoverageRow,
    GlyphStore,
    Kind,
    PositionInWord,
    ReviewStatus,
    Sample,
    StrokeData,
    Target,
    check_sample_em,
)
from hwfont_schema.emspace import png_size

from font_gen.qa import Finding, Severity

Pos = Literal["init", "medi", "fina", "isol"]

POS_FROM_STORE: dict[PositionInWord, Pos] = {
    PositionInWord.initial: "init",
    PositionInWord.medial: "medi",
    PositionInWord.final: "fina",
    PositionInWord.isolated: "isol",
}

_POS_FROM_NEIGHBOURS: dict[tuple[bool, bool], Pos] = {
    (True, True): "medi",
    (True, False): "fina",
    (False, True): "init",
    (False, False): "isol",
}

RASTER_SCALE_MISMATCH = "raster_scale_mismatch"


@dataclass(frozen=True)
class LoadedSample:
    sample: Sample
    strokes: StrokeData | None
    raster: bytes | None
    position: Pos
    flags: tuple[str, ...]


@dataclass(frozen=True)
class LoadResult:
    samples: tuple[LoadedSample, ...]
    targets: tuple[Target, ...]
    coverage: tuple[CoverageRow, ...]
    digest: str
    findings: tuple[Finding, ...]


def is_letterish(ch: str | None) -> bool:
    return ch is not None and (ch.isalpha() or ch == "'")


def shaping_position(context: Context) -> Pos:
    """Position as the font's @LETTER boundary set sees it, from the sample's neighbours."""
    return _POS_FROM_NEIGHBOURS[(is_letterish(context.left_neighbor), is_letterish(context.right_neighbor))]


def store_digest(store: GlyphStore) -> str:
    samples = "\n".join(f"{s.id}\t{s.model_dump_json()}" for s in sorted(store.all_samples(), key=lambda s: s.id))
    targets = "\n".join(f"{t.label}\t{t.kind.value}\t{t.required_count}" for t in store.targets())
    return hashlib.sha256((samples + "\n" + targets).encode("utf-8")).hexdigest()


def _load_one(
    store: GlyphStore, sample: Sample, target_kinds: dict[str, set[Kind]], findings: list[Finding]
) -> LoadedSample | None:
    flags: list[str] = []

    def flag(severity: Severity, code: str, message: str, data: dict | None = None) -> None:
        flags.append(code)
        findings.append(Finding(severity, code, message, label=sample.label, sample_id=sample.id, data=data or {}))

    try:
        strokes = store.load_strokes(sample)
        raster = store.load_raster(sample)
        if raster is not None:
            png_size(raster)
    except (OSError, ValueError) as exc:
        flag("warn", "sidecar_missing", f"sample {sample.id}: sidecar missing or unparseable ({exc})")
        return None
    if strokes is None and raster is None:
        flag("warn", "sample_no_ink", f"sample {sample.id}: neither strokes nor raster")
        return None

    for violation in check_sample_em(sample, strokes, raster):
        code = violation.split(": ", 1)[0]
        if code == RASTER_SCALE_MISMATCH:
            flag("warn", RASTER_SCALE_MISMATCH, f"sample {sample.id}: raster dropped, {violation}", {"violation": violation})
            raster = None
        else:
            flag("warn", "convention_violation", f"sample {sample.id}: {violation}", {"violation": violation})
    if strokes is None and raster is None:
        return None

    expected = Kind.single if len(sample.label) == 1 else Kind.ligature
    kinds = target_kinds.get(sample.label)
    if sample.kind != expected or (kinds is not None and sample.kind not in kinds):
        target_kind = ",".join(sorted(k.value for k in kinds)) if kinds is not None else None
        flag(
            "warn", "kind_mismatch",
            f"sample {sample.id}: kind {sample.kind.value} vs label rule {expected.value}, target {target_kind}",
            {"kind": sample.kind.value, "label_rule": expected.value, "target": target_kind},
        )

    position = shaping_position(sample.context)
    stored = POS_FROM_STORE[sample.context.position_in_word]
    if stored != position:
        flag(
            "info", "position_rederived",
            f"sample {sample.id}: stored position {stored}, shaping position {position}",
            {"stored": stored, "derived": position},
        )
    return LoadedSample(sample, strokes, raster, position, tuple(flags))


def load_store(store: GlyphStore) -> LoadResult:
    """Read every accepted sample (sorted by id) with its sidecars and the findings raised while loading."""
    targets = tuple(store.targets())
    target_kinds: dict[str, set[Kind]] = {}
    for t in targets:
        target_kinds.setdefault(t.label, set()).add(t.kind)
    findings: list[Finding] = []
    loaded: list[LoadedSample] = []
    for sample in sorted(store.all_samples(ReviewStatus.accepted), key=lambda s: s.id):
        ls = _load_one(store, sample, target_kinds, findings)
        if ls is not None:
            loaded.append(ls)
    return LoadResult(
        samples=tuple(loaded),
        targets=targets,
        coverage=tuple(store.coverage()),
        digest=store_digest(store),
        findings=tuple(sorted(findings, key=lambda f: (f.sample_id or "", f.code))),
    )

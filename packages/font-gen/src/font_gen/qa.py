"""QA: the one Finding record every stage reports with, the severity policy, the report model, the post-build
self-check (spec §11 steps 1–6) and the HarfBuzz expectation suite generated from a GlyphPlan.

Every other module imports `Finding` from here, so the plan/feature/naming helpers this module needs are imported
inside the functions that use them (a module-level import would be circular).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import uharfbuzz as hb
from fontTools.pens.areaPen import AreaPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont

from font_gen.nib import UPM

if TYPE_CHECKING:
    from hwfont_schema import CoverageRow

    from font_gen.load import Pos
    from font_gen.metrics import VMetrics
    from font_gen.outline import Outline
    from font_gen.select import GlyphPlan

Severity = Literal["error", "warn", "info"]

TTF_BOUNDS_TOL = 3
PROBE_PREFERENCE = "xzqjkvwypmbdlrucnoeasith"
OFF3: dict[str, bool] = {"calt": False, "liga": False, "clig": False}
MAX_REPORTED_FAILURES = 10
_FS_USE_TYPO_METRICS = 0x80

ERROR_CODES = frozenset({
    "empty_font", "store_invalid", "glyph_name_invalid", "feature_compile_failed", "reload_failed",
    "metrics_inconsistent_binary", "shaping_check_failed", "output_exists",
})
WARN_CODES = frozenset({
    "target_no_samples", "sample_no_ink", "sidecar_missing", "convention_violation", "kind_mismatch",
    "raster_scale_mismatch", "outline_degenerate", "outline_empty", "outline_fit_fallback", "outline_unstable",
    "outline_fragmented", "raster_fallback", "raster_unavailable", "metrics_inconsistent", "metrics_suspicious",
    "advance_outlier", "x_height_outlier", "baseline_nonzero", "pressure_partial", "ring_singleton",
    "ligature_component_missing", "proof_png_failed",
})
INFO_CODES = frozenset({
    "target_below_required", "ring_short", "ring_surplus", "ring_not_prime", "marginal_used", "bad_sample_excluded",
    "position_rederived", "ligature_restricted", "ligature_overlap", "dot_rendered", "stroke_degenerate",
    "outline_tiny_contour", "nib_width_assumed", "space_advance_assumed", "proof_png_skipped", "font_stats",
})
_SEVERITY_OF: dict[str, Severity] = {
    **{code: "error" for code in ERROR_CODES},
    **{code: "warn" for code in WARN_CODES},
    **{code: "info" for code in INFO_CODES},
}
_RANK: dict[str, int] = {"error": 0, "warn": 1, "info": 2}


@dataclass(frozen=True)
class Finding:
    severity: Severity
    code: str
    message: str
    label: str | None = None
    sample_id: str | None = None
    glyph: str | None = None
    data: dict = field(default_factory=dict)


def severity_of(code: str) -> Severity:
    return _SEVERITY_OF[code]


# ---------- shaping helpers ----------


def shape_glyphs(font_bytes: bytes, text: str, features: dict[str, bool] | None = None) -> list[tuple[str, int, int, int]]:
    """Shape `text` with HarfBuzz: `(glyph name, x_advance, x_offset, y_offset)` per output glyph."""
    face = hb.Face(hb.Blob(font_bytes))
    font = hb.Font(face)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, features or {})
    order = TTFont(BytesIO(font_bytes)).getGlyphOrder()
    return [(order[info.codepoint], pos.x_advance, pos.x_offset, pos.y_offset)
            for info, pos in zip(buf.glyph_infos, buf.glyph_positions)]


def shape_names(font_bytes: bytes, text: str, features: dict[str, bool] | None = None) -> list[str]:
    return [name for name, _, _, _ in shape_glyphs(font_bytes, text, features)]


# ---------- report model ----------


@dataclass(frozen=True)
class CoverageReportRow:
    label: str
    kind: str
    required: int
    accepted: int
    eligible: int
    met: bool
    met_eligible: bool


@dataclass(frozen=True)
class QAReport:
    em_convention_version: int
    store_digest: str
    family: str
    created_at: str
    ring_n: int
    findings: tuple[Finding, ...]
    coverage: tuple[CoverageReportRow, ...]
    stats: dict[str, int | str]
    written: tuple[str, ...]

    def counts(self) -> dict[Severity, int]:
        out: dict[Severity, int] = {"error": 0, "warn": 0, "info": 0}
        for f in self.findings:
            out[f.severity] += 1
        return out

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.severity == "warn" for f in self.findings)


def exit_code(report: QAReport, strict: bool) -> int:
    if report.has_errors:
        return 1
    if strict and report.has_warnings:
        return 2
    return 0


def coverage_rows(coverage: Sequence[CoverageRow], eligible: dict[str, int]) -> tuple[CoverageReportRow, ...]:
    """The store's coverage (accepted samples) joined with the plan's eligible counts (accepted and usable)."""
    rows = []
    for row in coverage:
        n = eligible.get(row.label, 0)
        rows.append(CoverageReportRow(row.label, row.kind, row.required, row.accepted, n, row.met, n >= row.required))
    return tuple(rows)


def _finding_key(f: Finding) -> tuple[int, str, str, str, str]:
    return (_RANK[f.severity], f.code, f.label or "", f.sample_id or "", f.glyph or "")


def sorted_findings(findings: Sequence[Finding]) -> list[Finding]:
    return sorted(findings, key=_finding_key)


def report_json(report: QAReport) -> str:
    data = asdict(report)
    data["findings"] = [asdict(f) for f in sorted_findings(report.findings)]
    return json.dumps(data, sort_keys=True, indent=2)


def summarize(report: QAReport, font_path: str | None) -> str:
    """Console summary: a status line, then one line per warn/error."""
    c = report.counts()
    qa = f"QA: {c['error']} errors, {c['warn']} warnings, {c['info']} infos"
    if font_path is None:
        lines = [f"No font written: {qa}"]
    else:
        qa_path = next((p for p in report.written if p.endswith(".qa.json")), None) or str(Path(font_path).with_suffix(".qa.json"))
        n, k = report.stats.get("glyphs", 0), report.stats.get("ligatures", 0)
        lines = [f"Wrote {font_path}: {n} glyphs, {k} ligatures, ring {report.ring_n}; {qa} → {qa_path}"]
    for f in sorted_findings(report.findings):
        if f.severity != "info":
            subject = f.label or f.glyph or f.sample_id or "-"
            lines.append(f"  {f.severity}  {f.code}  {subject}  {f.message}")
    return "\n".join(lines)


# ---------- generated shaping suite (spec §11 step 5) ----------

Mode = Literal["exact", "stripped", "noalt", "distinct", "ends_distinct", "ends_equal"]
_ALT_SUFFIX = re.compile(r"\.alt\d+")


@dataclass(frozen=True)
class Expectation:
    text: str
    expect: tuple[str, ...]
    mode: Mode
    features: dict[str, bool] | None
    note: str


def _ligature_labels(plan: GlyphPlan) -> dict[str, str]:
    """ligature glyph name → its label (the typed characters)."""
    by_name = plan.by_name()
    return {lig: by_name[lig].label for lig in plan.ligatures}


def probe_letter(plan: GlyphPlan, components: tuple[str, ...]) -> str | None:
    """First `PROBE_PREFERENCE` letter in the cmap whose neighbourhood with `components` extends no other ligature."""
    by_name = plan.by_name()
    label = "".join(by_name[c].label for c in components)
    others = {lab for lig, lab in _ligature_labels(plan).items() if lab != label}
    for probe in PROBE_PREFERENCE:
        if ord(probe) not in plan.cmap:
            continue
        if not any(other in probe + label or other in label + probe for other in others):
            return probe
    return None


def _positional_name(plan: GlyphPlan, base: str, pos: Pos) -> str:
    return plan.positional.get(base, {}).get(pos, base)


def _word_forms(plan: GlyphPlan, word: str) -> tuple[str, ...]:
    """Base-or-positional glyph name for each letter of an all-letter `word` typed in isolation (no `.alt`)."""
    from font_gen.features import _context_of
    from font_gen.naming import base_name

    n = len(word)
    return tuple(_positional_name(plan, base_name(ch), _context_of(i, 1, n)) for i, ch in enumerate(word))


def _probe_words(probe: str, core: str) -> dict[Pos, str]:
    return {"medi": probe + core + probe, "fina": probe + core, "init": core + probe, "isol": core}


def shaping_expectations(plan: GlyphPlan, cycle_mode: str = "stream") -> list[Expectation]:
    from font_gen.features import ligature_overlaps, resolve_ligatures
    from font_gen.naming import POS_ORDER, base_name

    by_name = plan.by_name()
    lig_labels = _ligature_labels(plan)
    exps: list[Expectation] = []

    # every ligature in each of its allowed positions forms, and in each disallowed one does not
    for lig in sorted(plan.ligatures):
        components, allowed = plan.ligatures[lig]
        label = lig_labels[lig]
        probe = probe_letter(plan, components)
        words = _probe_words(probe, label) if probe is not None else {"isol": label}
        for pos in POS_ORDER:
            if pos not in words:
                continue
            verb = "forms" if pos in allowed else "does not form"
            exps.append(Expectation(words[pos], resolve_ligatures(plan, words[pos]), "stripped", None,
                                    f"ligature {label} {verb} in {pos}"))

    # every competing pair resolves as documented (Task 14's oracle)
    for f in ligature_overlaps(plan):
        a, b = f.data["pair"]
        exps.append(Expectation(f.data["word"], tuple(f.data["expect"]), "stripped", None, f"overlap {a}/{b}"))

    # doubled text shapes to two distinct glyphs wherever a ring of >= 2 cycles
    for (label, pos), ring in sorted(plan.rings.items(), key=lambda kr: (kr[0][0], kr[0][1] or "")):
        if pos is not None or len(ring) < 2:
            continue
        doubled = label + label
        if any(other in doubled for other in lig_labels.values() if other != label):
            continue
        if len(label) == 1:
            exps.append(Expectation(doubled, (), "distinct", None, f"ring {label} cycles"))
        elif plan.ligatures.get(ring[0], ((), frozenset()))[1] == frozenset(POS_ORDER):
            exps.append(Expectation(doubled, (), "distinct", None, f"ligature ring {label} cycles"))

    # every positional ring: its probe yields the positional base
    for base in sorted(plan.positional):
        label = by_name[base].label
        probe = probe_letter(plan, (base,))
        if probe is None:
            continue
        words = _probe_words(probe, label)
        for pos in POS_ORDER:
            if pos in plan.positional[base]:
                exps.append(Expectation(words[pos], _word_forms(plan, words[pos]), "noalt", None, f"{base} takes .{pos}"))

    # the §9.9 fixed rows that apply: transparent skipping at depth 1 and 2, reset at depth 3. The letter must be a
    # letter and the period must be transparent (a singleton ring): a cycling punctuation ring steps the counter
    # itself, so with a 2-variant letter `a. a` wraps back to the base and `a...a` does not reset
    cmapped = {chr(cp) for cp in plan.cmap}
    first = next((lab for (lab, pos), ring in sorted(plan.rings.items(), key=lambda kr: kr[0][0])
                  if pos is None and len(lab) == 1 and lab.isalpha() and len(ring) >= 2 and lab in cmapped), None)
    rows: list[tuple[str, Mode, str]] = []
    if first is not None and cycle_mode == "word":  # no @TRANSPARENT rules: the counter restarts at every word
        rows.append((f"{first} {first}", "ends_equal", "word-mode reset"))
    elif first is not None:
        rows.append((f"{first} {first}", "ends_distinct", "skip depth 1"))
        if "." in cmapped and base_name(".") in plan.transparent:
            rows.append((f"{first}. {first}", "ends_distinct", "skip depth 2"))
            rows.append((f"{first}...{first}", "ends_equal", "depth-3 reset"))
    for text, mode, note in rows:
        if all(ch in cmapped for ch in text):
            exps.append(Expectation(text, (), mode, None, note))

    # with every feature off, every cmap'd character shapes to its base glyph
    for cp in sorted(plan.cmap):
        exps.append(Expectation(chr(cp), (plan.cmap[cp],), "exact", dict(OFF3), f"U+{cp:04X} base glyph"))

    for e in exps:
        assert all(ord(ch) in plan.cmap for ch in e.text), f"expectation {e.text!r} uses a character outside the cmap"
    return exps


def _reduce(names: list[str], mode: Mode) -> list[str]:
    from font_gen.naming import strip_suffixes

    if mode == "stripped":
        return [strip_suffixes(n) for n in names]
    if mode == "noalt":
        return [_ALT_SUFFIX.sub("", n) for n in names]
    return names


def check_expectation(font_bytes: bytes, e: Expectation) -> str | None:
    """`None` when the font shapes `e.text` as expected, else a message saying what it did instead."""
    names = shape_names(font_bytes, e.text, e.features)
    got = _reduce(names, e.mode)
    if e.mode in ("exact", "stripped", "noalt"):
        ok = tuple(got) == e.expect
        want = " ".join(e.expect)
    elif e.mode == "distinct":
        ok = len(set(got)) == len(got)
        want = "all distinct"
    elif e.mode == "ends_distinct":
        ok = len(got) >= 2 and got[0] != got[-1]
        want = "first and last distinct"
    else:
        ok = len(got) >= 2 and got[0] == got[-1]
        want = "first and last equal"
    if ok:
        return None
    return f"{e.text!r} ({e.note}, {e.mode}): expected {want}; got {' '.join(names)}"


# ---------- post-build self-check (spec §11 steps 1–6) ----------


def _contour_areas(glyph) -> list[float]:
    rec = RecordingPen()
    glyph.draw(rec)
    areas: list[float] = []
    contour: list = []
    for op, args in rec.value:
        contour.append((op, args))
        if op in ("closePath", "endPath"):
            pen = AreaPen()
            for cop, cargs in contour:
                getattr(pen, cop)(*cargs)
            areas.append(pen.value)
            contour = []
    return areas


def _glyph_count(tt: TTFont, is_ttf: bool) -> int:
    if is_ttf:
        return len(tt["glyf"].glyphs)
    return len(tt["CFF "].cff.topDictIndex[0].CharStrings)


def _check_glyph(tt: TTFont, name: str, outline: Outline, hm: tuple[int, int], is_ttf: bool) -> list[str]:
    gs = tt.getGlyphSet()
    glyph = gs[name]
    problems: list[str] = []
    bp = BoundsPen(gs)
    glyph.draw(bp)
    b = bp.bounds
    got = None if b is None else (math.floor(b[0]), math.floor(b[1]), math.ceil(b[2]), math.ceil(b[3]))
    want = outline.bounds
    if (got is None) != (want is None):
        problems.append(f"bounds {got} != {want}")
    elif got is not None and want is not None:
        tol = TTF_BOUNDS_TOL if is_ttf else 0
        if any(abs(g - w) > tol for g, w in zip(got, want)):
            problems.append(f"bounds {got} != {want}" + (f" (tol {tol})" if tol else ""))
    adv, lsb = tt["hmtx"][name]
    if adv != hm[0]:
        problems.append(f"advance {adv} != {hm[0]}")
    if not is_ttf and lsb != hm[1]:
        problems.append(f"lsb {lsb} != {hm[1]}")
    areas = _contour_areas(glyph)
    if areas:
        largest = max(areas, key=abs)
        if (largest < 0) != is_ttf:
            problems.append(f"largest contour area {largest:.0f} has the wrong direction for {'TrueType' if is_ttf else 'CFF'}")
    return problems


def _feature_lookups(gsub) -> dict[str, dict[str, set[int]]]:
    """script tag → feature tag → lookup indices, from each script's default language system."""
    records = gsub.FeatureList.FeatureRecord
    out: dict[str, dict[str, set[int]]] = {}
    for sr in gsub.ScriptList.ScriptRecord:
        tags = out.setdefault(sr.ScriptTag, {})
        langsys = sr.Script.DefaultLangSys
        for idx in (langsys.FeatureIndex if langsys is not None else []):
            fr = records[idx]
            tags.setdefault(fr.FeatureTag, set()).update(fr.Feature.LookupListIndex)
    return out


def _required_features(plan: GlyphPlan) -> set[str]:
    required: set[str] = set()
    if plan.ligatures:
        required |= {"liga", "clig"}
    default_alts = any(len(ring) >= 2 for (_, pos), ring in plan.rings.items() if pos is None)
    if plan.positional or any(len(ring) >= 2 for ring in plan.rings.values()):
        required.add("calt")
    if plan.positional or default_alts:
        required.add("aalt")
    return required


def _check_gsub(tt: TTFont, plan: GlyphPlan) -> list[str]:
    required = _required_features(plan)
    if "GSUB" not in tt:
        return [f"no GSUB table; features {sorted(required)} are required"] if required else []
    by_script = _feature_lookups(tt["GSUB"].table)
    problems: list[str] = []
    for script in ("DFLT", "latn"):
        if script not in by_script:
            problems.append(f"script {script} missing from GSUB")
            continue
        missing = sorted(required - set(by_script[script]))
        if missing:
            problems.append(f"features {missing} missing under {script}")
    tags = by_script.get("latn") or by_script.get("DFLT") or {}
    liga, clig, calt = tags.get("liga"), tags.get("clig"), tags.get("calt")
    if liga != clig:
        problems.append(f"liga lookups {sorted(liga or ())} != clig lookups {sorted(clig or ())}")
    if liga and calt and max(liga) >= min(calt):
        problems.append(f"ligature lookups {sorted(liga)} do not all precede calt lookups {sorted(calt)}")
    return problems


def self_check(
    font_bytes: bytes,
    plan: GlyphPlan,
    outlines: dict[str, Outline],
    hmetrics: dict[str, tuple[int, int]],
    vmetrics: VMetrics,
    ts: int,
    *,
    is_ttf: bool = False,
    cycle_mode: str = "stream",
) -> list[Finding]:
    """Spec §11 steps 1–6 on the font bytes; every step's crash becomes that step's finding, never an exception."""
    findings: list[Finding] = []
    names = plan.names()

    # 1. reload, glyph order, glyph count
    try:
        tt = TTFont(BytesIO(font_bytes))
        order = tt.getGlyphOrder()
        if order != names:
            raise ValueError(f"glyph order differs from the plan ({len(order)} vs {len(names)} glyphs)")
        count = _glyph_count(tt, is_ttf)
        if count != len(names):
            raise ValueError(f"{count} glyph programs for {len(names)} glyphs")
    except Exception as exc:  # any reload problem is the finding
        return [Finding("error", "reload_failed", f"font does not reload cleanly: {exc}")]

    # 2. per-glyph outlines and horizontal metrics against what was built
    try:
        for name in names:
            problems = _check_glyph(tt, name, outlines[name], hmetrics[name], is_ttf)
            if problems:
                findings.append(Finding("error", "metrics_inconsistent_binary", f"{name}: {'; '.join(problems)}", glyph=name))
    except Exception as exc:
        findings.append(Finding("error", "metrics_inconsistent_binary", f"glyph check crashed: {exc}"))

    # 3. vertical metrics, head
    try:
        os2, hhea, head = tt["OS/2"], tt["hhea"], tt["head"]
        max_top = max((o.bounds[3] for o in outlines.values() if o.bounds is not None), default=0)
        checks = [
            (os2.sTypoAscender == hhea.ascent == vmetrics.ascender, f"ascender OS/2 {os2.sTypoAscender} / hhea {hhea.ascent} / plan {vmetrics.ascender}"),
            (os2.sTypoDescender == hhea.descent == vmetrics.descender, f"descender OS/2 {os2.sTypoDescender} / hhea {hhea.descent} / plan {vmetrics.descender}"),
            (os2.usWinAscent >= max_top, f"usWinAscent {os2.usWinAscent} < max yMax {max_top}"),
            (bool(os2.fsSelection & _FS_USE_TYPO_METRICS), f"fsSelection {os2.fsSelection:#x} lacks USE_TYPO_METRICS"),
            (head.unitsPerEm == UPM, f"unitsPerEm {head.unitsPerEm} != {UPM}"),
            (head.created == head.modified == ts, f"head.created {head.created} / head.modified {head.modified} != {ts}"),
        ]
        for ok, message in checks:
            if not ok:
                findings.append(Finding("error", "metrics_inconsistent_binary", message))
    except Exception as exc:
        findings.append(Finding("error", "metrics_inconsistent_binary", f"table check crashed: {exc}"))

    # 4. GSUB feature registration and lookup order
    try:
        for message in _check_gsub(tt, plan):
            findings.append(Finding("error", "shaping_check_failed", message))
    except Exception as exc:
        findings.append(Finding("error", "shaping_check_failed", f"GSUB check crashed: {exc}"))

    # 5. the generated HarfBuzz expectation suite
    try:
        failures = [m for e in shaping_expectations(plan, cycle_mode) if (m := check_expectation(font_bytes, e)) is not None]
        if failures:
            findings.append(Finding("error", "shaping_check_failed",
                                    f"{len(failures)} shaping expectation(s) failed; first: {failures[0]}",
                                    data={"failed": len(failures), "failures": failures[:MAX_REPORTED_FAILURES]}))
    except Exception as exc:
        findings.append(Finding("error", "shaping_check_failed", f"shaping suite crashed: {exc}"))

    return findings

"""CLI and pipeline orchestration (spec §11, §13): `build | demo | proof`, output-path discipline, atomic writes.

`run_build` is the pure pipeline (no disk). Everything that touches disk lives in `write_outputs`/`proof_command`:
outputs are checked before the store is read, nothing is written until the font bytes passed the self-check, every
file goes through `<path>.tmp` + `os.replace`, and a failed run removes only the files it wrote itself.

Every pipeline function is bound as a module-level name and called through it, so tests can monkeypatch them.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from fontTools.feaLib.error import FeatureLibError
from hwfont_schema import EM_CONVENTION_VERSION, GlyphStore, Kind

from font_gen.autotrace import raster_to_outline
from font_gen.build import BuildMeta, build_font, build_ttf, head_timestamp
from font_gen.features import generate_features, ligature_overlaps
from font_gen.load import load_store
from font_gen.metrics import collect_vertical, hmetrics, ligature_carets, lowercase_advances, metric_findings, space_advance
from font_gen.nib import NIB_EM, nib_units
from font_gen.outline import Outline, notdef_outline, outline_from_strokes
from font_gen.proof import default_rows, proof_rows_from_font, render_png, render_proof
from font_gen.qa import Finding, QAReport, coverage_rows, exit_code, report_json, self_check, summarize
from font_gen.select import NOTDEF, POS_MIN, RING_MAX, RING_MIN, RING_N, SPACE, GlyphPlan, plan_glyphs, sample_index
from font_gen.synth import demo_spec, synth_store

DEFAULT_CREATED_AT = "1970-01-01T00:00:00Z"
BASELINE_TOL = 0.1
DEMO_FAMILY = "Demo Hand"
DEMO_STEM = "Demo"


@dataclass(frozen=True)
class BuildOptions:
    family: str = "Micah Hand"
    style: str = "Regular"
    nib_em: float = NIB_EM
    ring_n: int = RING_N
    pos_min: int = POS_MIN
    space_em: float | None = None
    smooth_sigma: float = 0.25
    cycle_mode: str = "stream"
    ttf: bool = False
    created_at: str = DEFAULT_CREATED_AT
    strict: bool = False
    proof: bool = True


@dataclass(frozen=True)
class BuildResult:
    otf: bytes | None
    ttf: bytes | None
    fea: str | None
    report: QAReport
    proof_svg: str | None
    plan: GlyphPlan | None


# ---------- pipeline ----------


def _report(opts: BuildOptions, digest: str, findings: Sequence[Finding], coverage=(), stats=None) -> QAReport:
    return QAReport(
        em_convention_version=EM_CONVENTION_VERSION, store_digest=digest, family=opts.family,
        created_at=opts.created_at, ring_n=opts.ring_n, findings=tuple(findings), coverage=tuple(coverage),
        stats=dict(stats or {}), written=(),
    )


def run_build(store: GlyphStore, opts: BuildOptions) -> BuildResult:
    """Store → font bytes, `.fea`, proof SVG and QA report, all in memory.

    A store that fails schema validation yields a `store_invalid` report with `plan=None` (console only). A feaLib
    compile error and self-check findings become report errors with `otf=None`. Any other exception propagates.
    """
    try:
        loaded = load_store(store)
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        return BuildResult(None, None, None, _report(opts, "", [Finding("error", "store_invalid", str(exc))]), None, None)

    findings: list[Finding] = list(loaded.findings)
    plan, plan_findings = plan_glyphs(loaded, ring_n=opts.ring_n, pos_min=opts.pos_min)
    findings.extend(plan_findings)
    coverage = coverage_rows(loaded.coverage, plan.eligible)
    fea: str | None = None

    def stop() -> BuildResult:
        return BuildResult(None, None, fea, _report(opts, loaded.digest, findings, coverage), None, plan)

    if any(f.severity == "error" for f in findings):
        return stop()

    samples = sample_index(loaded)
    nib = nib_units(opts.nib_em)
    outlines: dict[str, Outline] = {NOTDEF.name: notdef_outline(), SPACE.name: Outline.empty()}
    tier_c = False
    for g in plan.glyphs:
        if g.sample_id is None:
            continue
        ls = samples[g.sample_id]
        ctx = {"nib": nib, "smooth_sigma": opts.smooth_sigma, "label": g.label, "sample_id": g.sample_id, "glyph": g.name}
        if ls.strokes is not None:
            result = outline_from_strokes(ls.strokes, ls.sample.metrics, **ctx)
            tier_c = tier_c or result.tier == "C"
        else:
            result = raster_to_outline(ls.raster, ls.sample.metrics, **ctx)
        outlines[g.name] = result.outline
        findings.extend(result.findings)
        baseline = ls.sample.metrics.baseline
        if abs(baseline) > BASELINE_TOL:
            findings.append(Finding("warn", "baseline_nonzero",
                                    f"sample {g.sample_id}: baseline {baseline:.3f} em; ink translated by {-baseline:.3f} em",
                                    label=g.label, sample_id=g.sample_id, glyph=g.name, data={"baseline": baseline}))
    if tier_c:
        findings.append(Finding("info", "nib_width_assumed",
                                f"samples without pressure or width drawn with the default nib {nib:g} units"))

    space, space_finding = space_advance(lowercase_advances(plan, samples), opts.space_em)
    if space_finding is not None:
        findings.append(space_finding)
    hm = hmetrics(plan, outlines, samples, space)
    vm = collect_vertical(plan, outlines, samples)
    carets = ligature_carets(plan, hm)
    findings.extend(metric_findings(plan, outlines, samples, opts.nib_em))

    digest12 = loaded.digest[:12]
    fea = generate_features(plan, carets, digest12=digest12, cycle_mode=opts.cycle_mode)
    findings.extend(ligature_overlaps(plan))
    meta = BuildMeta(opts.family, opts.style, digest12, opts.created_at)
    ts = head_timestamp(opts.created_at)

    try:
        otf = build_font(plan, outlines, hm, vm, fea, meta)
    except FeatureLibError as exc:
        findings.append(Finding("error", "feature_compile_failed", str(exc)))
        return stop()
    checks = self_check(otf, plan, outlines, hm, vm, ts, cycle_mode=opts.cycle_mode)
    ttf: bytes | None = None
    if not checks and opts.ttf:
        try:
            ttf = build_ttf(plan, outlines, hm, vm, fea, meta)
        except FeatureLibError as exc:
            findings.append(Finding("error", "feature_compile_failed", str(exc)))
            return stop()
        checks = self_check(ttf, plan, outlines, hm, vm, ts, is_ttf=True, cycle_mode=opts.cycle_mode)
    if checks:
        findings.extend(checks)
        return stop()

    proof_svg = None
    if opts.proof:
        missing = [f.label for f in findings if f.code == "target_no_samples" and f.label is not None]
        proof_svg = render_proof(otf, default_rows(plan, samples), missing)

    stats = {
        "glyphs": len(plan.glyphs),
        "ligatures": len({g.label for g in plan.glyphs if g.kind == Kind.ligature}),
        "rings": len(plan.rings),
        "samples": sum(1 for g in plan.glyphs if g.sample_id is not None),
    }
    findings.append(Finding("info", "font_stats",
                            f"{stats['glyphs']} glyphs, {stats['ligatures']} ligatures, {stats['rings']} rings, "
                            f"{stats['samples']} samples", data=dict(stats)))
    return BuildResult(otf, ttf, fea, _report(opts, loaded.digest, findings, coverage, stats), proof_svg, plan)


# ---------- output-path discipline ----------


def output_paths(out: Path, *, ttf: bool, qa: Path | None, proof: Path | None, png: bool) -> dict[str, Path]:
    paths = {"otf": out, "fea": out.with_suffix(".fea")}
    if ttf:
        paths["ttf"] = out.with_suffix(".ttf")
    paths["qa"] = qa or out.with_suffix(".qa.json")
    paths["proof"] = proof or out.with_suffix(".proof.svg")
    if png:
        paths["png"] = paths["proof"].with_suffix(".png")
    return paths


def check_outputs(paths: Sequence[Path], force: bool) -> Finding | None:
    existing = [str(p) for p in paths if p.exists()]
    if not existing or force:
        return None
    return Finding("error", "output_exists", f"refusing to overwrite without --force: {', '.join(existing)}",
                   data={"paths": existing})


def write_atomic(path: Path, data: bytes, written: list[Path]) -> None:
    """Write `<path>.tmp` beside the target, then `os.replace` it: a pre-existing file changes only when complete."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    written.append(path)


def remove_written(written: list[Path]) -> None:
    for path in written:
        path.unlink(missing_ok=True)


def write_outputs(result: BuildResult, paths: dict[str, Path], png: bool, strict: bool) -> tuple[QAReport, list[Path]]:
    report = result.report
    png_bytes: bytes | None = None
    if png and result.proof_svg is not None:
        try:
            png_bytes = render_png(result.proof_svg)
        except ImportError:
            report = replace(report, findings=(*report.findings, Finding(
                "info", "proof_png_skipped", "cairosvg is not installed; proof PNG skipped")))
        except Exception as exc:
            report = replace(report, findings=(*report.findings, Finding(
                "warn", "proof_png_failed", f"proof PNG rendering failed: {exc}")))

    if exit_code(report, strict) != 0:
        queue = [(paths["qa"], None)]
    else:
        queue = [
            (paths["fea"], result.fea.encode("utf-8")),
            (paths["otf"], result.otf),
            (paths.get("ttf"), result.ttf),
            (paths["qa"], None),
            (paths.get("proof"), result.proof_svg.encode("utf-8") if result.proof_svg is not None else None),
            (paths.get("png"), png_bytes),
        ]
        queue = [(p, d) for p, d in queue if p is not None and (d is not None or p == paths["qa"])]
    report = replace(report, written=tuple(str(p) for p, _ in queue))

    written: list[Path] = []
    current = None
    try:
        for current, data in queue:
            write_atomic(current, data if data is not None else report_json(report).encode("utf-8"), written)
    except OSError as exc:
        remove_written(written)
        if current != paths["qa"]:
            failed = replace(report, stats={**report.stats, "write_error": str(exc)}, written=())
            write_atomic(paths["qa"], report_json(failed).encode("utf-8"), [])
        raise
    return report, written


# ---------- commands ----------


def _console_refusal(opts: BuildOptions, finding: Finding) -> int:
    print(summarize(_report(opts, "", [finding]), None))
    return 1


def _finish(result: BuildResult, paths: dict[str, Path], png: bool, strict: bool) -> int:
    report, written = write_outputs(result, paths, png, strict)
    print(summarize(report, str(paths["otf"]) if paths["otf"] in written else None))
    return exit_code(report, strict)


def build_command(args: argparse.Namespace) -> int:
    opts = BuildOptions(
        family=args.family, style=args.style, nib_em=args.nib_em, ring_n=args.ring, pos_min=args.pos_min,
        space_em=args.space_em, smooth_sigma=args.smooth_sigma, cycle_mode=args.cycle_mode, ttf=args.ttf,
        created_at=args.created_at, strict=args.strict,
    )
    paths = output_paths(Path(args.out), ttf=args.ttf, qa=Path(args.qa) if args.qa else None,
                         proof=Path(args.proof) if args.proof else None, png=args.proof_png)
    refusal = check_outputs(list(paths.values()), args.force)
    if refusal is not None:
        return _console_refusal(opts, refusal)
    store = GlyphStore.open(args.store)
    try:
        result = run_build(store, opts)
    finally:
        store.close()
    if result.plan is None:  # store_invalid: console only, nothing written
        print(summarize(result.report, None))
        return 1
    return _finish(result, paths, args.proof_png, args.strict)


def demo_command(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    opts = BuildOptions(family=DEMO_FAMILY, ttf=args.ttf)
    paths = output_paths(out_dir / f"{DEMO_STEM}.otf", ttf=args.ttf, qa=None, proof=None, png=True)
    refusal = check_outputs(list(paths.values()), False)
    if refusal is not None:
        return _console_refusal(opts, refusal)
    with tempfile.TemporaryDirectory() as tmp:
        store = synth_store(Path(tmp) / "store", demo_spec(), seed=args.seed, rasters=False)
        try:
            result = run_build(store, opts)
        finally:
            store.close()
    out_dir.mkdir(parents=True, exist_ok=True)
    return _finish(result, paths, True, False)


def _text_rows(path: str) -> list[tuple[str, str]]:
    lines = [line for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return [(f"text {n}", line) for n, line in enumerate(lines, start=1)]


def proof_command(args: argparse.Namespace) -> int:
    svg_path = Path(args.out)
    targets = [svg_path] + ([svg_path.with_suffix(".png")] if args.png else [])
    refusal = check_outputs(targets, args.force)
    if refusal is not None:
        print(summarize(_report(BuildOptions(), "", [refusal]), None))
        return 1
    font_bytes = Path(args.font).read_bytes()
    rows = [] if args.only_text else proof_rows_from_font(font_bytes)
    if args.text:
        rows += _text_rows(args.text)
    svg = render_proof(font_bytes, rows, [])
    findings: list[Finding] = []
    outputs = [(svg_path, svg.encode("utf-8"))]
    if args.png:
        try:
            outputs.append((targets[1], render_png(svg)))
        except ImportError:
            findings.append(Finding("info", "proof_png_skipped", "cairosvg is not installed; proof PNG skipped"))
        except Exception as exc:
            findings.append(Finding("warn", "proof_png_failed", f"proof PNG rendering failed: {exc}"))
    written: list[Path] = []
    try:
        for path, data in outputs:
            write_atomic(path, data, written)
    except OSError:
        remove_written(written)
        raise
    print(f"Wrote {', '.join(str(p) for p in written)}: {len(rows)} rows")
    for f in findings:
        print(f"  {f.severity}  {f.code}  -  {f.message}")
    return 0


# ---------- argparse ----------


def _ring(value: str) -> int:
    n = int(value)
    if not RING_MIN <= n <= RING_MAX:
        raise argparse.ArgumentTypeError(f"--ring must be in [{RING_MIN}, {RING_MAX}], got {n}")
    return n


def _pos_min(value: str) -> int:
    n = int(value)
    if n < 2:
        raise argparse.ArgumentTypeError(f"--pos-min must be >= 2, got {n}")
    return n


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="font-gen", description="Build an OpenType font from a reviewed glyph store.")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="build a font from a glyph store")
    b.add_argument("--store", required=True, help="glyph store directory")
    b.add_argument("--out", required=True, help="output .otf path")
    b.add_argument("--ttf", action="store_true", help="also write a TrueType .ttf")
    b.add_argument("--family", default=BuildOptions.family)
    b.add_argument("--style", default=BuildOptions.style)
    b.add_argument("--nib-em", type=float, default=BuildOptions.nib_em)
    b.add_argument("--ring", type=_ring, default=BuildOptions.ring_n)
    b.add_argument("--pos-min", type=_pos_min, default=BuildOptions.pos_min)
    b.add_argument("--space-em", type=float, default=None)
    b.add_argument("--smooth-sigma", type=float, default=BuildOptions.smooth_sigma)
    b.add_argument("--cycle-mode", choices=("stream", "word"), default=BuildOptions.cycle_mode)
    b.add_argument("--proof", default=None, help="proof SVG path (default <out>.proof.svg)")
    b.add_argument("--proof-png", action="store_true", help="also rasterise the proof (needs cairosvg)")
    b.add_argument("--qa", default=None, help="QA report path (default <out>.qa.json)")
    b.add_argument("--strict", action="store_true", help="treat warnings as failures (exit 2, no font)")
    b.add_argument("--created-at", default=DEFAULT_CREATED_AT, help="ISO-8601 head timestamp")
    b.add_argument("--force", action="store_true", help="overwrite existing outputs")
    b.set_defaults(func=build_command)

    d = sub.add_parser("demo", help="build a font from the synthetic demo store")
    d.add_argument("--out", required=True, help="output directory")
    d.add_argument("--seed", type=int, default=1)
    d.add_argument("--ttf", action="store_true")
    d.set_defaults(func=demo_command)

    p = sub.add_parser("proof", help="render a proof sheet from a built font")
    p.add_argument("--font", required=True)
    p.add_argument("--out", required=True, help="output SVG path")
    p.add_argument("--text", default=None, help="file with one proof row per non-empty line")
    p.add_argument("--only-text", action="store_true", help="only the --text rows")
    p.add_argument("--png", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=proof_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

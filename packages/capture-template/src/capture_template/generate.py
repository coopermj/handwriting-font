from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
from pathlib import Path

from hwfont_schema import Kind

from capture_template.corpus import default_corpus_paths, load_corpus
from capture_template.layout import LayoutModel, PageConfig, build_layout, rows_per_page
from capture_template.pdf import render_pdf
from capture_template.planner import PlanResult, plan
from capture_template.sidecar_out import build_sidecar
from capture_template.targets import default_targets, load_target_spec


DEFAULT_PAGES = 12
MAX_QUOTE_CHARS = 240


class UnmetCoverageError(RuntimeError):
    """Raised when the planned booklet cannot meet every target's required count."""


def _default_config() -> PageConfig:
    return PageConfig(
        width_px=1404,
        height_px=1872,
        dpi=226,
        margin_px=80,
        prompt_font_px=28,
        prompt_gap_px=12,
        line_height_px=70,
        row_pitch_px=150,
        # sized for handwriting, not the print font. Measured on a real reMarkable
        # capture (2026-11-14): the writer's advance is ~20px per unit across the
        # 1244px writing row, and 60+ units per row overflowed it. 44 chars is
        # ~35 units, ~60% of the row: the owner asked for 1/2 to 2/3 of the old
        # 80-char rows. Override per writer with `--line-chars`.
        max_line_chars=44,
    )


def generate(
    target_spec_path: str | Path | None,
    corpus_dir: str | Path | None,
    out_dir: str | Path,
    config: PageConfig | None = None,
    force: bool = False,
    allow_unmet: bool = False,
    pages: int = DEFAULT_PAGES,
    line_chars: int | None = None,
) -> PlanResult:
    """Plan, lay out and render a capture booklet into `out_dir`.

    `line_chars` overrides the config's `max_line_chars` (the per-row prompt budget
    the writer copies by hand); None keeps the config's value.
    """
    if pages < 1:
        raise ValueError(f"pages must be >= 1, got {pages}")
    if line_chars is not None and line_chars < 1:
        raise ValueError(f"line_chars must be >= 1, got {line_chars}")
    out_dir = Path(out_dir)
    if out_dir.exists() and not force:
        raise FileExistsError(f"output dir already exists: {out_dir} (use force=True to overwrite)")
    created = not out_dir.exists()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        config = config or _default_config()
        if line_chars is not None:
            config = dataclasses.replace(config, max_line_chars=line_chars)
        targets = (
            load_target_spec(target_spec_path) if target_spec_path is not None else default_targets()
        )

        charset = {t.label for t in targets if t.kind == Kind.single}
        sources = (
            sorted(Path(corpus_dir).glob("*.txt")) if corpus_dir is not None else default_corpus_paths()
        )
        candidates = load_corpus(list(sources), charset, max_chars=MAX_QUOTE_CHARS)

        target_lines = pages * rows_per_page(config)
        result = plan(targets, candidates, target_lines=target_lines, max_line_chars=config.max_line_chars)

        if not result.all_met and not allow_unmet:
            unmet = [r.label for r in result.coverage if not r.met]
            raise UnmetCoverageError(
                f"Booklet cannot meet all targets. Unmet ({len(unmet)}): {', '.join(unmet)}"
            )

        model: LayoutModel = build_layout(result.lines, targets, config)

        render_pdf(model, out_dir / "capture.pdf")
        (out_dir / "capture.sidecar.json").write_text(
            build_sidecar(model).model_dump_json(), encoding="utf-8"
        )
        (out_dir / "targets.json").write_text(
            json.dumps([t.model_dump(mode="json") for t in targets]), encoding="utf-8"
        )

        unmet = [r.label for r in result.coverage if not r.met]
        genuine = sum(1 for line in result.lines if not line.is_drill)
        drills = len(result.lines) - genuine
        print(
            f"Generated {len(result.lines)} prompt lines "
            f"({genuine} genuine, {drills} drills) across {len(model.pages)} page(s)."
        )
        print(f"Coverage: {sum(r.met for r in result.coverage)}/{len(result.coverage)} targets met.")
        if unmet:
            print(f"UNMET targets ({len(unmet)}): {', '.join(unmet)}")
        return result
    except Exception:
        if created:
            shutil.rmtree(out_dir, ignore_errors=True)
        raise


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a handwriting-capture PDF + sidecar.")
    parser.add_argument("--target-spec", default=None, help="YAML/JSON target spec (default: built-in)")
    parser.add_argument("--corpus-dir", default=None, help="dir of .txt sources (default: bundled)")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--force", action="store_true", help="overwrite an existing output dir")
    parser.add_argument("--allow-unmet", action="store_true", help="write the booklet even if some targets are unmet")
    parser.add_argument(
        "--pages", type=_positive_int, default=DEFAULT_PAGES,
        help=(
            f"variety floor in pages (default: {DEFAULT_PAGES}); the booklet always "
            "grows as needed to meet glyph/ligature coverage"
        ),
    )
    parser.add_argument(
        "--line-chars", type=_positive_int, default=None,
        help=(
            f"max prompt characters per row (default: {_default_config().max_line_chars}, sized "
            "for ~20px handwriting advance across the 1244px row); lower it for a large hand, "
            "raise it for a small one"
        ),
    )
    args = parser.parse_args(argv)

    try:
        result = generate(
            target_spec_path=args.target_spec,
            corpus_dir=args.corpus_dir,
            out_dir=args.out,
            force=args.force,
            allow_unmet=args.allow_unmet,
            pages=args.pages,
            line_chars=args.line_chars,
        )
    except UnmetCoverageError as e:
        print(e)
        return 1
    return 0 if result.all_met else 1


if __name__ == "__main__":
    raise SystemExit(main())

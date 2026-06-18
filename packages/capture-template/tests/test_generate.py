import json

import pytest
from hwfont_schema import CaptureSidecar

import capture_template as ct
from capture_template.generate import generate, main, UnmetCoverageError
from capture_template.layout import PageConfig


def _config() -> PageConfig:
    return PageConfig(
        width_px=1000,
        height_px=1400,
        dpi=226,
        margin_px=50,
        prompt_font_px=24,
        prompt_gap_px=10,
        line_height_px=60,
        row_pitch_px=130,
    )


def test_top_level_exports_present():
    for name in ["generate", "PageConfig", "default_targets", "plan", "build_layout", "__version__", "DEFAULT_PAGES"]:
        assert hasattr(ct, name), f"missing export: {name}"


def test_generate_end_to_end(tmp_path):
    # tiny target spec + tiny corpus. `include` is the full lowercase alphabet so the
    # corpus passes the charset filter; letters absent from the corpus get drill-filled.
    # The corpus lines have no terminal punctuation (which is not in the charset), so each
    # line is a single candidate sentence.
    spec = {
        "glyphs": {"count": 1, "include": "abcdefghijklmnopqrstuvwxyz"},
        "ligatures": {"count": 1, "items": ["at"]},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "c.txt").write_text(
        "the cat sat on a mat and the bat ran\n"
        "bees see oats as the trees sway today\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    result = generate(
        target_spec_path=spec_path,
        corpus_dir=corpus_dir,
        out_dir=out_dir,
        config=_config(),
    )

    assert (out_dir / "capture.pdf").exists()
    assert (out_dir / "targets.json").exists()
    sidecar_path = out_dir / "capture.sidecar.json"
    assert sidecar_path.exists()

    # sidecar validates against the contract
    sidecar = CaptureSidecar.model_validate_json(sidecar_path.read_text(encoding="utf-8"))
    region_count = sum(len(p.regions) for p in sidecar.pages)
    assert region_count == len(result.lines)
    assert result.all_met is True


def test_generate_refuses_existing_out_dir_without_force(tmp_path):
    spec = {"glyphs": {"count": 1, "include": "abc "}, "ligatures": {"count": 1, "items": []}}
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "c.txt").write_text("a b c cab cab.\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()  # already exists
    with pytest.raises(FileExistsError):
        generate(target_spec_path=spec_path, corpus_dir=corpus_dir, out_dir=out_dir, config=_config())


def test_main_returns_zero_when_all_met(tmp_path):
    spec = {
        "glyphs": {"count": 1, "include": "abcdefghijklmnopqrstuvwxyz"},
        "ligatures": {"count": 1, "items": ["at"]},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "c.txt").write_text(
        "the cat sat on a mat and the bat ran\n", encoding="utf-8"
    )
    rc = main([
        "--target-spec", str(spec_path),
        "--corpus-dir", str(corpus_dir),
        "--out", str(tmp_path / "out0"),
    ])
    assert rc == 0


def test_generate_raises_on_unmet_without_allow(tmp_path):
    long_lig = "a" * 61  # longer than the default drill_budget (60) -> can never be placed
    spec = {
        "glyphs": {"count": 1, "include": "abcdefghijklmnopqrstuvwxyz"},
        "ligatures": {"count": 1, "items": [long_lig]},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "c.txt").write_text(
        "the cat sat on a mat and the bat ran\n", encoding="utf-8"
    )
    out_dir = tmp_path / "out_raises"
    with pytest.raises(UnmetCoverageError):
        generate(
            target_spec_path=spec_path,
            corpus_dir=corpus_dir,
            out_dir=out_dir,
            config=_config(),
        )
    assert not out_dir.exists()


def test_generate_writes_with_allow_unmet(tmp_path):
    long_lig = "a" * 61  # longer than the default drill_budget (60) -> can never be placed
    spec = {
        "glyphs": {"count": 1, "include": "abcdefghijklmnopqrstuvwxyz"},
        "ligatures": {"count": 1, "items": [long_lig]},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "c.txt").write_text(
        "the cat sat on a mat and the bat ran\n", encoding="utf-8"
    )
    out_dir = tmp_path / "out_allow"
    result = generate(
        target_spec_path=spec_path,
        corpus_dir=corpus_dir,
        out_dir=out_dir,
        config=_config(),
        allow_unmet=True,
    )
    assert result.all_met is False
    assert (out_dir / "capture.pdf").exists()


def test_main_returns_one_when_targets_unmet(tmp_path):
    long_lig = "a" * 61  # longer than the default drill_budget (60) -> can never be placed
    spec = {
        "glyphs": {"count": 1, "include": "abcdefghijklmnopqrstuvwxyz"},
        "ligatures": {"count": 1, "items": [long_lig]},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "c.txt").write_text(
        "the cat sat on a mat and the bat ran\n", encoding="utf-8"
    )
    rc = main([
        "--target-spec", str(spec_path),
        "--corpus-dir", str(corpus_dir),
        "--out", str(tmp_path / "out1"),
    ])
    assert rc == 1


def _big_corpus_dir(tmp_path):
    nato = (
        "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
        "mike november oscar papa quebec romeo sierra tango uniform victor whiskey "
        "xray yankee zulu"
    ).split()
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    sents = [f"the quick brown fox jumps over {w} and lazy dogs run." for w in nato]
    (corpus_dir / "c.txt").write_text("\n".join(sents), encoding="utf-8")
    return corpus_dir


def _alpha_spec(tmp_path):
    # include a period so sentences split, and so '.' is a satisfiable single-glyph target
    spec = {
        "glyphs": {"count": 1, "include": "abcdefghijklmnopqrstuvwxyz."},
        "ligatures": {"count": 1, "items": []},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    return spec_path


def test_generate_pages_controls_booklet_size(tmp_path):
    cfg = PageConfig(
        width_px=1000, height_px=1400, dpi=226, margin_px=50,
        prompt_font_px=24, prompt_gap_px=10, line_height_px=60, row_pitch_px=130,
    )  # rows_per_page == 10
    result = generate(
        target_spec_path=_alpha_spec(tmp_path),
        corpus_dir=_big_corpus_dir(tmp_path),
        out_dir=tmp_path / "out",
        config=cfg,
        pages=2,
    )
    assert len(result.lines) == 20  # 2 pages * 10 rows, filled with genuine prose
    assert all(not line.is_drill for line in result.lines)


def test_generate_rejects_pages_below_one(tmp_path):
    with pytest.raises(ValueError):
        generate(
            target_spec_path=None, corpus_dir=None, out_dir=tmp_path / "out", pages=0
        )
    assert not (tmp_path / "out").exists()  # invalid arg rejected before any dir is created


def test_main_accepts_pages_flag(tmp_path):
    rc = main([
        "--target-spec", str(_alpha_spec(tmp_path)),
        "--corpus-dir", str(_big_corpus_dir(tmp_path)),
        "--out", str(tmp_path / "out"),
        "--pages", "2",
    ])
    assert rc == 0


def test_default_corpus_yields_multipage_mostly_genuine_booklet(tmp_path):
    from hwfont_schema import CaptureSidecar, Kind

    from capture_template.corpus import default_corpus_paths, load_corpus
    from capture_template.targets import default_targets

    targets = default_targets()
    charset = {t.label for t in targets if t.kind == Kind.single}
    pool = load_corpus(default_corpus_paths(), charset, max_chars=240)
    assert len(pool) >= 40  # healthy genuine pool

    result = generate(target_spec_path=None, corpus_dir=None, out_dir=tmp_path / "out")
    genuine = sum(1 for line in result.lines if not line.is_drill)
    assert genuine >= 35  # the booklet is mostly real prose, not drills

    sidecar = CaptureSidecar.model_validate_json(
        (tmp_path / "out" / "capture.sidecar.json").read_text(encoding="utf-8")
    )
    assert len(sidecar.pages) >= 3  # genuinely multi-page


def test_generate_wraps_long_quotation_into_multiple_regions(tmp_path):
    from hwfont_schema import CaptureSidecar

    spec = {
        "glyphs": {"count": 1, "include": "abcdefghijklmnopqrstuvwxyz."},
        "ligatures": {"count": 1, "items": []},
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    # one long quotation (~150 chars) that must wrap across several lines
    long_quote = (
        "the quick brown fox jumps over the lazy dog while the calm grey cat "
        "watched from the warm window sill and the bright moon rose slowly."
    )
    (corpus_dir / "c.txt").write_text(long_quote + "\n", encoding="utf-8")

    cfg = PageConfig(
        width_px=1000, height_px=1400, dpi=226, margin_px=50,
        prompt_font_px=24, prompt_gap_px=10, line_height_px=60, row_pitch_px=130,
        max_line_chars=40,
    )
    result = generate(
        target_spec_path=spec_path, corpus_dir=corpus_dir, out_dir=tmp_path / "out",
        config=cfg, pages=4,
    )
    # one genuine entry, but it renders as multiple sidecar regions (wrapped rows)
    genuine = [line for line in result.lines if not line.is_drill]
    assert len(genuine) == 1
    sidecar = CaptureSidecar.model_validate_json(
        (tmp_path / "out" / "capture.sidecar.json").read_text(encoding="utf-8")
    )
    total_regions = sum(len(p.regions) for p in sidecar.pages)
    # the long quote alone is ~130 chars at a 40-char budget -> >= 3 wrapped regions
    assert total_regions >= 3

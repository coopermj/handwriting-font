import json

import pytest
from hwfont_schema import CaptureSidecar

import capture_template as ct
from capture_template.generate import generate, main
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
    for name in ["generate", "PageConfig", "default_targets", "plan", "build_layout", "__version__"]:
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

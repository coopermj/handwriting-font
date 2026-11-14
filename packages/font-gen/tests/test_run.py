import io
import json
import sys
from datetime import datetime, timezone

import pytest
from fontTools.misc.timeTools import timestampSinceEpoch
from fontTools.ttLib import TTFont

import font_gen.run as run_mod
from font_gen.naming import strip_suffixes
from font_gen.qa import shape_names
from font_gen.run import main


def _codes(qa_path):
    return {f["code"] for f in json.loads(qa_path.read_text())["findings"]}


def _absent(monkeypatch, *mods):
    for m in mods:
        monkeypatch.setitem(sys.modules, m, None)


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory):
    """One seed-1 demo build with the optional dependencies hidden; shared by the demo tests."""
    mp = pytest.MonkeyPatch()
    for m in ("cairosvg", "skimage", "skimage.measure", "skimage.filters"):
        mp.setitem(sys.modules, m, None)
    out = tmp_path_factory.mktemp("demo")
    rc = main(["demo", "--out", str(out)])
    mp.undo()
    assert rc == 0
    return out


def test_demo_writes_font_fea_report_and_proof_without_optional_deps(demo_dir):
    for name in ("Demo.otf", "Demo.fea", "Demo.qa.json", "Demo.proof.svg"):
        assert (demo_dir / name).exists(), name
    assert not (demo_dir / "Demo.proof.png").exists() and not (demo_dir / "Demo.ttf").exists()
    codes = _codes(demo_dir / "Demo.qa.json")
    assert "proof_png_skipped" in codes and not any(c.startswith("raster_") for c in codes)
    assert len(TTFont(io.BytesIO((demo_dir / "Demo.otf").read_bytes())).getGlyphOrder()) > 500


def test_demo_seed_and_ttf(tmp_path, demo_dir):
    assert main(["demo", "--out", str(tmp_path), "--seed", "2", "--ttf"]) == 0
    assert (tmp_path / "Demo.otf").read_bytes() != (demo_dir / "Demo.otf").read_bytes()   # the seed reaches synth_store
    assert "glyf" in TTFont(io.BytesIO((tmp_path / "Demo.ttf").read_bytes()))


def test_build_on_e2e_store_reports_exact_codes_and_shapes(tmp_path, e2e_store, capsys):
    out = tmp_path / "Hand.otf"
    rc = main(["build", "--store", str(e2e_store.root), "--out", str(out)])
    assert rc == 0 and out.exists() and (tmp_path / "Hand.fea").exists() and (tmp_path / "Hand.proof.svg").exists()
    assert capsys.readouterr().out.splitlines()[0].startswith(f"Wrote {out}: ")
    assert _codes(tmp_path / "Hand.qa.json") == {
        "bad_sample_excluded", "dot_rendered", "font_stats", "kind_mismatch", "ligature_overlap", "ligature_restricted",
        "marginal_used", "nib_width_assumed", "outline_degenerate", "outline_tiny_contour", "raster_fallback", "ring_short",
        "ring_singleton", "space_advance_assumed", "stroke_degenerate", "target_below_required", "target_no_samples"}
    # outline_tiny_contour: the raster-only q's binarisation pinhole, kept and reported info per spec §7.5 step 6 / §7.6
    report = json.loads((tmp_path / "Hand.qa.json").read_text())
    assert report["em_convention_version"] == 1 and len(report["store_digest"]) == 64
    assert any(r["label"] == "b" and r["met"] and not r["met_eligible"] for r in report["coverage"])
    font = out.read_bytes()
    assert TTFont(io.BytesIO(font))["name"].getDebugName(5).endswith(report["store_digest"][:12])   # loaded.digest[:12] reaches BuildMeta
    assert len(shape_names(font, "the")) == 3
    assert [strip_suffixes(n) for n in shape_names(font, "fi")] == ["f", "i"]   # fi is medial-only in the store
    assert strip_suffixes(shape_names(font, "afia")[1]) == "f_i"                 # ...and the ligature glyph cycles


def test_corrupt_sample_row_is_store_invalid_and_writes_nothing(tmp_path, capsys):
    import sqlite3
    from font_gen.synth import synth_store
    synth_store(tmp_path / "st", {"a": {"medi": 2}}, seed=1, rasters=False).close()
    con = sqlite3.connect(tmp_path / "st" / "store.db")
    con.execute("UPDATE sample SET data = json_set(data, '$.metrics.advance', -1) WHERE id = (SELECT min(id) FROM sample)")  # advance > 0 is schema-enforced
    con.commit()
    con.close()
    rc = main(["build", "--store", str(tmp_path / "st"), "--out", str(tmp_path / "H.otf")])
    out = capsys.readouterr().out
    assert rc == 1 and out.startswith("No font written") and "store_invalid" in out
    assert not list(tmp_path.glob("H.*"))


def test_feature_compile_failure_is_loud_and_writes_only_the_report(tmp_path, e2e_store, monkeypatch):
    broken = "languagesystem DFLT dflt;\nlanguagesystem latn dflt;\nlookup EMPTY {\n} EMPTY;\nfeature calt {\n  sub a' lookup EMPTY;\n} calt;\n"
    monkeypatch.setattr(run_mod, "generate_features", lambda *a, **k: broken)   # the omitted-empty-lookup bug feature_compile_failed guards
    out = tmp_path / "Hand.otf"
    rc = main(["build", "--store", str(e2e_store.root), "--out", str(out)])
    assert rc == 1 and not out.exists() and not (tmp_path / "Hand.fea").exists() and not (tmp_path / "Hand.proof.svg").exists()
    assert "feature_compile_failed" in _codes(tmp_path / "Hand.qa.json")


def test_baseline_nonzero_is_translated_and_flagged(tmp_path):
    from fontTools.pens.boundsPen import BoundsPen
    from hwfont_schema import BBox, Context, GlyphStore, Kind, Metrics, PositionInWord, Quality, ReviewStatus, Sample
    from hwfont_schema.strokes import Contour, StrokeData, StrokePoint
    from font_gen.run import BuildOptions, run_build
    store = GlyphStore.create(tmp_path / "st")
    sample = Sample(
        id="s1", label="a", kind=Kind.single,
        context=Context(source_word="a", left_neighbor=None, right_neighbor=None, position_in_word=PositionInWord.isolated),
        metrics=Metrics(baseline=0.15, x_height=0.45, advance=0.6, bbox=BBox(x=0.05, y=0.15, w=0.5, h=0.5)),   # strokes and bbox agree in the sample's own frame
        quality=Quality.good, review_status=ReviewStatus.accepted, capture_session_id="s", created_at="1970-01-01T00:00:00Z")
    store.add_sample(sample, strokes=StrokeData(contours=[Contour(points=[StrokePoint(x=0.05, y=0.15), StrokePoint(x=0.55, y=0.65)])]))
    result = run_build(store, BuildOptions())
    store.close()
    codes = {f.code for f in result.report.findings}
    assert {"baseline_nonzero", "convention_violation"} <= codes and "metrics_inconsistent" not in codes   # the em check flags |baseline| > 0.1 too
    gs = TTFont(io.BytesIO(result.otf)).getGlyphSet()
    bp = BoundsPen(gs)
    gs["a"].draw(bp)
    assert -24 <= bp.bounds[1] <= -21 and 521 <= bp.bounds[3] <= 524   # ink translated by -150 units: y 0..500 plus the NIB/2 round caps


def test_png_render_failure_is_a_warning_not_a_failure(tmp_path, fixture_store, monkeypatch):
    import types
    fake = types.ModuleType("cairosvg")

    def svg2png(**kwargs):
        raise RuntimeError("no cairo")
    fake.svg2png = svg2png
    monkeypatch.setitem(sys.modules, "cairosvg", fake)
    out = tmp_path / "F.otf"
    assert main(["build", "--store", str(fixture_store.root), "--out", str(out), "--proof-png"]) == 0
    assert out.exists() and (tmp_path / "F.proof.svg").exists() and not (tmp_path / "F.proof.png").exists()
    report = json.loads((tmp_path / "F.qa.json").read_text())
    assert any(f["code"] == "proof_png_failed" and f["severity"] == "warn" for f in report["findings"])


def test_every_build_flag_reaches_the_pipeline(tmp_path, fixture_store, monkeypatch):
    _absent(monkeypatch, "cairosvg")
    base = ["build", "--store", str(fixture_store.root)]
    assert main(base + ["--out", str(tmp_path / "D.otf")]) == 0
    default = TTFont(io.BytesIO((tmp_path / "D.otf").read_bytes()))
    assert main(base + ["--out", str(tmp_path / "S.otf"), "--space-em", "0.3", "--cycle-mode", "word", "--family", "X Y", "--style", "Bold",
                        "--qa", str(tmp_path / "q.json"), "--proof", str(tmp_path / "p.svg")]) == 0
    tt = TTFont(io.BytesIO((tmp_path / "S.otf").read_bytes()))
    assert tt["hmtx"]["space"] == (300, 0) and "space_advance_assumed" not in _codes(tmp_path / "q.json")
    fea = (tmp_path / "S.fea").read_text()
    assert "@TRANSPARENT" not in fea[fea.index("lookup CYCLE {"):fea.index("} CYCLE;")]
    assert tt["name"].getDebugName(1) == "X Y" and tt["name"].getDebugName(2) == "Bold" and tt["name"].getDebugName(6) == "XY-Bold"
    assert (tmp_path / "p.svg").exists() and not (tmp_path / "S.qa.json").exists() and not (tmp_path / "S.proof.svg").exists()
    assert main(base + ["--out", str(tmp_path / "N.otf"), "--nib-em", "0.06"]) == 0
    nib = TTFont(io.BytesIO((tmp_path / "N.otf").read_bytes()))
    assert 6 <= default["hmtx"]["a"][1] - nib["hmtx"]["a"][1] <= 9          # lsb = xMin - nib/2: 22.5 -> 30, +-1 from rounding
    assert main(base + ["--out", str(tmp_path / "G.otf"), "--smooth-sigma", "0"]) == 0
    assert (tmp_path / "G.otf").read_bytes() != (tmp_path / "D.otf").read_bytes()
    assert main(base + ["--out", str(tmp_path / "T.otf"), "--ttf"]) == 0
    assert "glyf" in TTFont(io.BytesIO((tmp_path / "T.ttf").read_bytes()))


def test_overwrite_refusal_never_touches_a_pre_existing_file(tmp_path, e2e_store, capsys):
    out = tmp_path / "Hand.otf"
    out.write_bytes(b"OLD")
    rc = main(["build", "--store", str(e2e_store.root), "--out", str(out)])
    assert rc == 1 and out.read_bytes() == b"OLD" and not (tmp_path / "Hand.qa.json").exists()
    assert "output_exists" in capsys.readouterr().out
    rc = main(["build", "--store", str(e2e_store.root), "--out", str(out), "--force"])
    assert rc == 0 and out.read_bytes()[:4] == b"OTTO"


def test_sabotaged_ttf_step_leaves_pre_existing_file_untouched_and_no_tmp(tmp_path, e2e_store, monkeypatch):
    out = tmp_path / "Hand.otf"
    out.write_bytes(b"OLD")

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(run_mod, "build_ttf", boom)
    rc = main(["build", "--store", str(e2e_store.root), "--out", str(out), "--force", "--ttf"])
    assert rc == 1 and out.read_bytes() == b"OLD" and not (tmp_path / "Hand.ttf").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_post_write_failure_removes_only_files_written_this_run(tmp_path, e2e_store, monkeypatch):
    proof = tmp_path / "Hand.proof.svg"
    proof.write_bytes(b"KEEP")
    real = run_mod.write_atomic

    def flaky(path, data, written):
        if path.suffix == ".json":
            raise OSError("disk full")
        real(path, data, written)
    monkeypatch.setattr(run_mod, "write_atomic", flaky)
    rc = main(["build", "--store", str(e2e_store.root), "--out", str(tmp_path / "Hand.otf"), "--force"])
    assert rc == 1 and proof.read_bytes() == b"KEEP"
    assert not (tmp_path / "Hand.otf").exists() and not (tmp_path / "Hand.fea").exists() and not list(tmp_path.glob("*.tmp"))
    assert not (tmp_path / "Hand.qa.json").exists()   # the report itself was the failing write: nothing to re-write


def test_post_write_failure_on_the_proof_rewrites_the_report(tmp_path, e2e_store, monkeypatch):
    proof = tmp_path / "Hand.proof.svg"
    proof.write_bytes(b"KEEP")
    real = run_mod.write_atomic

    def flaky(path, data, written):
        if path.suffix == ".svg":
            raise OSError("disk full")
        real(path, data, written)
    monkeypatch.setattr(run_mod, "write_atomic", flaky)
    rc = main(["build", "--store", str(e2e_store.root), "--out", str(tmp_path / "Hand.otf"), "--force"])
    assert rc == 1 and proof.read_bytes() == b"KEEP"                                   # --force overwrites only on success
    assert not (tmp_path / "Hand.otf").exists() and not (tmp_path / "Hand.fea").exists() and not list(tmp_path.glob("*.tmp"))
    report = json.loads((tmp_path / "Hand.qa.json").read_text())
    assert report["stats"]["write_error"] == "disk full" and report["written"] == []


def test_strict_turns_warnings_into_exit_2_without_a_font(tmp_path, e2e_store):
    rc = main(["build", "--store", str(e2e_store.root), "--out", str(tmp_path / "Hand.otf"), "--strict"])
    assert rc == 2 and not (tmp_path / "Hand.otf").exists() and (tmp_path / "Hand.qa.json").exists()


def test_argparse_bounds_and_created_at(tmp_path, fixture_store):
    for argv in (["build", "--store", "x", "--out", "y.otf", "--ring", "14"], ["build", "--store", "x", "--out", "y.otf", "--ring", "1"],
                 ["build", "--store", "x", "--out", "y.otf", "--pos-min", "1"]):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2
    out = tmp_path / "F.otf"
    assert main(["build", "--store", str(fixture_store.root), "--out", str(out), "--created-at", "2026-11-13T12:00:00Z", "--ring", "5"]) == 0
    head = TTFont(io.BytesIO(out.read_bytes()))["head"]
    assert head.created == timestampSinceEpoch(datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc).timestamp())
    assert "@ST4" in (tmp_path / "F.fea").read_text() and "@ST5" not in (tmp_path / "F.fea").read_text()
    assert main(["build", "--store", str(tmp_path / "nope"), "--out", str(tmp_path / "G.otf")]) == 1


def test_proof_verb_matches_build_proof_and_text_options(tmp_path, fixture_store, monkeypatch, capsys):
    _absent(monkeypatch, "cairosvg")
    out = tmp_path / "F.otf"
    assert main(["build", "--store", str(fixture_store.root), "--out", str(out)]) == 0
    built = (tmp_path / "F.proof.svg").read_text()
    assert main(["proof", "--font", str(out), "--out", str(tmp_path / "p.svg")]) == 0
    standalone = (tmp_path / "p.svg").read_text()
    assert standalone.count("<path") > 0 and all(line in built for line in standalone.splitlines() if "<path" in line)
    # the bare binary yields every default row except the ligature rows (their words live only in the store)
    assert standalone.count("<g ") == built.count("<g ") - built.count('class="label">lig ')
    assert 'class="label">ring a</text>' in standalone and 'class="label">lig ' not in standalone
    words = tmp_path / "words.txt"
    words.write_text("hello\n\nworld\n")
    assert main(["proof", "--font", str(out), "--out", str(tmp_path / "p2.svg"), "--text", str(words)]) == 0
    assert (tmp_path / "p2.svg").read_text().count("<path") == standalone.count("<path") + 10
    assert main(["proof", "--font", str(out), "--out", str(tmp_path / "p3.svg"), "--text", str(words), "--only-text", "--png"]) == 0
    assert (tmp_path / "p3.svg").read_text().count("<path") == 10 and not (tmp_path / "p3.png").exists()
    assert "proof_png_skipped" in capsys.readouterr().out
    assert main(["proof", "--font", str(out), "--out", str(tmp_path / "p3.svg")]) == 1


@pytest.mark.parametrize("module", ["font_gen", "font_gen.run"])
def test_module_invocation_runs_the_cli(tmp_path, fixture_store, module):
    # `python -m font_gen.run ...` once imported the module and exited 0 without building anything
    import subprocess
    out = tmp_path / "M.otf"
    proc = subprocess.run([sys.executable, "-m", module, "build", "--store", str(fixture_store.root), "--out", str(out)],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "glyphs" in proc.stdout and out.exists() and (tmp_path / "M.qa.json").exists()
    if module == "font_gen":
        assert "RuntimeWarning" not in proc.stderr
    missing = subprocess.run([sys.executable, "-m", module, "build", "--store", str(tmp_path / "nope"), "--out", str(tmp_path / "X.otf")],
                             capture_output=True, text=True, timeout=60)
    assert missing.returncode == 1 and "No glyph store" in missing.stdout


def test_output_paths_always_name_an_otf_and_keep_siblings_distinct(tmp_path):
    from font_gen.run import check_outputs, normalized_otf, output_paths
    for given in ("Hand", "Hand.ttf", "Hand.otf"):
        assert output_paths(tmp_path / given, ttf=True, qa=None, proof=None, png=False)["otf"] == tmp_path / "Hand.otf"
    paths = output_paths(tmp_path / "Hand.ttf", ttf=True, qa=None, proof=None, png=True)
    assert paths == {"otf": tmp_path / "Hand.otf", "fea": tmp_path / "Hand.fea", "ttf": tmp_path / "Hand.ttf",
                     "qa": tmp_path / "Hand.qa.json", "proof": tmp_path / "Hand.proof.svg", "png": tmp_path / "Hand.proof.png"}
    assert len(set(paths.values())) == len(paths)
    explicit = output_paths(tmp_path / "Hand", ttf=False, qa=tmp_path / "r.json", proof=tmp_path / "p.svg", png=False)
    assert explicit["qa"] == tmp_path / "r.json" and explicit["proof"] == tmp_path / "p.svg"
    (tmp_path / "Hand.otf").write_bytes(b"OLD")
    (tmp_path / "Hand.ttf").write_bytes(b"OLD")
    refusal = check_outputs(list(paths.values()), False)
    assert refusal.data["paths"] == [str(tmp_path / "Hand.otf"), str(tmp_path / "Hand.ttf")]
    assert normalized_otf(tmp_path / "Hand") == (tmp_path / "Hand.otf", None)
    assert normalized_otf(tmp_path / "Hand.otf") == (tmp_path / "Hand.otf", None)
    path, finding = normalized_otf(tmp_path / "Hand.ttf")
    assert path == tmp_path / "Hand.otf" and finding.code == "output_path_normalized" and finding.severity == "info"
    assert finding.message == f"wrote {tmp_path / 'Hand.otf'} for --out {tmp_path / 'Hand.ttf'}"


def test_build_out_ttf_writes_distinct_otf_and_ttf(tmp_path, fixture_store, monkeypatch):
    _absent(monkeypatch, "cairosvg")
    assert main(["build", "--store", str(fixture_store.root), "--out", str(tmp_path / "Hand.ttf"), "--ttf"]) == 0
    assert (tmp_path / "Hand.otf").read_bytes()[:4] == b"OTTO" and "glyf" in TTFont(io.BytesIO((tmp_path / "Hand.ttf").read_bytes()))
    report = json.loads((tmp_path / "Hand.qa.json").read_text())
    assert "output_path_normalized" in {f["code"] for f in report["findings"]}
    assert str(tmp_path / "Hand.otf") in report["written"] and str(tmp_path / "Hand.ttf") in report["written"]
    assert main(["build", "--store", str(fixture_store.root), "--out", str(tmp_path / "Bare")]) == 0
    assert (tmp_path / "Bare.otf").exists() and not (tmp_path / "Bare").exists()
    assert "output_path_normalized" not in _codes(tmp_path / "Bare.qa.json")


def test_demo_force_overwrites_and_its_absence_refuses(tmp_path, capsys):
    assert main(["demo", "--out", str(tmp_path)]) == 0
    (tmp_path / "Demo.otf").write_bytes(b"OLD")
    capsys.readouterr()
    assert main(["demo", "--out", str(tmp_path)]) == 1 and "output_exists" in capsys.readouterr().out
    assert (tmp_path / "Demo.otf").read_bytes() == b"OLD"
    assert main(["demo", "--out", str(tmp_path), "--force"]) == 0
    assert (tmp_path / "Demo.otf").read_bytes()[:4] == b"OTTO"
    help_text = {a.dest: a.help for a in run_mod.build_parser()._subparsers._group_actions[0].choices["demo"]._actions}
    build_help = {a.dest: a.help for a in run_mod.build_parser()._subparsers._group_actions[0].choices["build"]._actions}
    assert help_text["force"] == build_help["force"]


def test_build_options_smooth_sigma_is_the_centerline_constant():
    from font_gen.centerline import SMOOTH_SIGMA
    assert run_mod.BuildOptions().smooth_sigma == SMOOTH_SIGMA
    assert run_mod.build_parser().parse_args(["build", "--store", "s", "--out", "o"]).smooth_sigma == SMOOTH_SIGMA


def test_normalized_otf_ignores_suffix_case_and_handles_dotted_stems(tmp_path):
    from font_gen.run import normalized_otf
    assert normalized_otf(tmp_path / "Hand.OTF") == (tmp_path / "Hand.otf", None)
    path, finding = normalized_otf(tmp_path / "Hand.v2")
    assert path == tmp_path / "Hand.otf" and finding is not None and finding.code == "output_path_normalized"

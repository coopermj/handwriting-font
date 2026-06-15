import json

import pytest
from hwfont_schema import Kind

from capture_template.targets import default_targets, load_target_spec


def test_default_targets_include_letters_and_ligatures():
    targets = default_targets()
    by_label = {t.label: t for t in targets}
    assert by_label["a"].kind == Kind.single
    assert by_label["a"].required_count == 12
    assert by_label["fi"].kind == Kind.ligature
    assert by_label["fi"].required_count == 8
    # labels are unique
    assert len({t.label for t in targets}) == len(targets)


def test_load_target_spec_builds_targets_with_overrides(tmp_path):
    spec = {
        "glyphs": {"count": 10, "include": "ab"},
        "ligatures": {"count": 5, "items": ["fi", "eft"]},
        "overrides": {"eft": 20},
    }
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    targets = load_target_spec(path)
    by_label = {t.label: t for t in targets}
    assert by_label["a"].required_count == 10
    assert by_label["a"].kind == Kind.single
    assert by_label["fi"].required_count == 5
    assert by_label["eft"].required_count == 20  # override wins
    assert by_label["eft"].kind == Kind.ligature


def test_load_target_spec_rejects_duplicate_label(tmp_path):
    # a single-char glyph and a ligature cannot share a label, and a glyph char
    # cannot repeat in `include`
    spec = {"glyphs": {"count": 1, "include": "aa"}, "ligatures": {"count": 1, "items": []}}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError):
        load_target_spec(path)


def test_load_target_spec_rejects_one_char_ligature(tmp_path):
    spec = {"glyphs": {"count": 1, "include": "a"}, "ligatures": {"count": 1, "items": ["x"]}}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(ValueError):
        load_target_spec(path)

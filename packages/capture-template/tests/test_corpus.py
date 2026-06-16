import pytest

from capture_template.corpus import load_corpus, sentence_in_charset, split_sentences


def test_split_sentences_splits_on_terminators_and_collapses_whitespace():
    text = "The quick brown fox.  It jumps!\nDoes it?  Yes."
    assert split_sentences(text) == [
        "The quick brown fox.",
        "It jumps!",
        "Does it?",
        "Yes.",
    ]


def test_sentence_in_charset():
    charset = set("abcdefghijklmnopqrstuvwxyz .")
    assert sentence_in_charset("a cat.", charset) is True
    assert sentence_in_charset("a c4t.", charset) is False


def test_load_corpus_filters_by_charset_and_length_and_dedupes(tmp_path):
    charset = set("abcdefghijklmnopqrstuvwxyz .")
    (tmp_path / "a.txt").write_text(
        "the cat sat on the warm mat.\n"  # ok
        "x9 bad chars here now today.\n"  # rejected: digit '9'
        "no.\n"  # rejected: too short
        "the cat sat on the warm mat.\n",  # duplicate of first
        encoding="utf-8",
    )
    got = load_corpus([tmp_path / "a.txt"], charset, min_chars=8, max_chars=90)
    assert got == ["the cat sat on the warm mat."]


def test_load_corpus_raises_when_no_usable_sentences(tmp_path):
    (tmp_path / "a.txt").write_text("123456789!!!\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_corpus([tmp_path / "a.txt"], set("abc ."), min_chars=4, max_chars=90)


from capture_template.corpus import default_corpus_paths


def test_default_corpus_paths_exist_and_are_nonempty():
    paths = default_corpus_paths()
    assert len(paths) >= 2
    for p in paths:
        assert p.exists()
        assert p.read_text(encoding="utf-8").strip()


def test_default_corpus_loads_usable_sentences():
    charset = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ .,!?'-;:")
    sentences = load_corpus(default_corpus_paths(), charset, min_chars=12, max_chars=120)
    assert len(sentences) >= 10


def test_default_corpus_has_healthy_usable_pool_at_default_budget():
    from capture_template.corpus import default_corpus_paths
    from capture_template.targets import default_targets
    from capture_template.planner import plan
    from hwfont_schema import Kind

    targets = default_targets()
    charset = {t.label for t in targets if t.kind == Kind.single}
    # generate() uses the default max_chars=90; the bundled corpus must yield a
    # healthy pool of natural sentences at that budget (not get mostly filtered out).
    candidates = load_corpus(default_corpus_paths(), charset, max_chars=90)
    assert len(candidates) >= 12

    result = plan(targets, candidates)
    natural = [line for line in result.lines if not line.is_drill]
    assert len(natural) >= 10


def test_normalize_typography_maps_to_ascii():
    from capture_template.corpus import normalize_typography

    assert (
        normalize_typography("“Hi—there’s…”")
        == "\"Hi-there's.\""
    )


def test_load_corpus_keeps_sentences_after_normalization(tmp_path):
    # curly quotes + em dash would fail an ASCII charset without normalization
    charset = set("abcdefghijklmnopqrstuvwxyz .,'\"-")
    (tmp_path / "a.txt").write_text(
        "“the cat—a fine cat’s tale” sat here.\n", encoding="utf-8"
    )
    got = load_corpus([tmp_path / "a.txt"], charset, min_chars=8, max_chars=90)
    assert got == ['"the cat-a fine cat\'s tale" sat here.']


def test_default_corpus_paths_globs_sorted_txt():
    from capture_template.corpus import default_corpus_paths

    paths = default_corpus_paths()
    assert len(paths) >= 2
    assert all(p.suffix == ".txt" for p in paths)
    assert all(p.exists() for p in paths)
    assert paths == sorted(paths)

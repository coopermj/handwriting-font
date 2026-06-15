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

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE = re.compile(r"\s+")


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for chunk in _SENTENCE_END.split(text):
        cleaned = _WHITESPACE.sub(" ", chunk).strip()
        if cleaned:
            sentences.append(cleaned)
    return sentences


def sentence_in_charset(sentence: str, charset: set[str]) -> bool:
    return all(ch == " " or ch in charset for ch in sentence)


def load_corpus(
    paths: list[Path],
    charset: set[str],
    min_chars: int = 12,
    max_chars: int = 90,
) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        for sentence in split_sentences(text):
            if not (min_chars <= len(sentence) <= max_chars):
                continue
            if not sentence_in_charset(sentence, charset):
                continue
            if sentence in seen:
                continue
            seen.add(sentence)
            result.append(sentence)
    if not result:
        raise ValueError("no usable sentences found in corpus sources")
    return result


def default_corpus_paths() -> list[Path]:
    base = resources.files("capture_template") / "data" / "corpus"
    return [Path(str(base / "literature.txt")), Path(str(base / "speeches.txt"))]

from __future__ import annotations

import re
from collections.abc import Sequence

from fontTools.agl import UV2AGL

from font_gen.load import Pos
from font_gen.qa import Finding

MAX_NAME_LEN = 31
NAME_RE = re.compile(r"^\.?[A-Za-z_][A-Za-z0-9._]*$")
POS_ORDER: tuple[Pos, ...] = ("init", "medi", "fina", "isol")
RESERVED = (".notdef", "space")
CMAP_ALIASES: dict[int, str] = {
    0x00A0: "space",
    0x2018: "quotesingle",
    0x2019: "quotesingle",
    0x201C: "quotedbl",
    0x201D: "quotedbl",
    0x2010: "hyphen",
    0x2013: "hyphen",
}

_SUFFIX_RE = re.compile(r"\.(alt\d+|init|medi|fina|isol)")
_ALT_RE = re.compile(r"\.alt(\d+)")


def char_name(ch: str) -> str:
    return UV2AGL.get(ord(ch), f"uni{ord(ch):04X}")


def base_name(label: str) -> str:
    if len(label) == 1:
        return char_name(label)
    return "_".join(char_name(c) for c in label)


def glyph_name(label: str, pos: Pos | None, slot: int) -> str:
    name = base_name(label)
    if pos is not None:
        name += f".{pos}"
    if slot:
        name += f".alt{slot}"
    return name


def strip_suffixes(name: str) -> str:
    return _SUFFIX_RE.sub("", name)


def label_key(label: str) -> tuple[int, ...]:
    return tuple(ord(c) for c in label)


def validate(names: Sequence[str], ring_n: int) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    for name in names:
        reason = None
        if not NAME_RE.match(name):
            reason = "does not match the glyph-name grammar"
        elif len(name) > MAX_NAME_LEN:
            reason = f"longer than {MAX_NAME_LEN} characters"
        elif name in seen:
            reason = "duplicates an earlier glyph name"
        else:
            m = _ALT_RE.search(name)
            if m and not 1 <= int(m.group(1)) <= ring_n - 1:
                reason = f"alt slot outside 1..{ring_n - 1}"
        seen.add(name)
        if reason:
            findings.append(Finding("error", "glyph_name_invalid", f"glyph name {name!r} {reason}", glyph=name))
    return findings
